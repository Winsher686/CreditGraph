"""信贷审批用例（应用层）。

把"审批这件事"的持久化编排集中在这里：提交申请、落库风险评估、记录审批链、
写审计日志、发起复审。图（`graph.py`）保持**纯**——不做任何数据库写入，
由接口层在一次工作流跑完后调用 `persist_run` 落库，避免 SQLite 检查点与业务表
争锁（历史教训：同一文件上的同步写会拖住 AsyncSqliteSaver 的连接）。
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..domain.credit import (
    DECISION_APPROVED,
    DECISION_AUTO_APPROVED,
    DECISION_AUTO_REJECTED,
    DECISION_CONDITIONAL,
    DECISION_PENDING,
    DECISION_REJECTED,
    LEVEL_LABELS,
)
from .ports import CreditRepositoryPort, KnowledgePort

# 申请状态
STATUS_SUBMITTED = "submitted"
STATUS_IN_REVIEW = "in_review"
STATUS_PENDING = "pending_review"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_CONDITIONAL = "conditional"

_TERMINAL_STATUSES = {STATUS_APPROVED, STATUS_REJECTED, STATUS_CONDITIONAL}

_DECISION_TO_STATUS = {
    DECISION_AUTO_APPROVED: STATUS_APPROVED,
    DECISION_APPROVED: STATUS_APPROVED,
    DECISION_AUTO_REJECTED: STATUS_REJECTED,
    DECISION_REJECTED: STATUS_REJECTED,
    DECISION_CONDITIONAL: STATUS_CONDITIONAL,
    DECISION_PENDING: STATUS_PENDING,
}


def status_for_decision(decision: str) -> str:
    return _DECISION_TO_STATUS.get(decision, STATUS_PENDING)


class CreditService:
    """审批用例：申请、落库、审计链、复审。"""

    def __init__(
        self,
        repository: CreditRepositoryPort,
        knowledge: KnowledgePort | None = None,
    ) -> None:
        self.repository = repository
        self.knowledge = knowledge

    # -- 提交 -----------------------------------------------------------------

    def submit(
        self,
        *,
        applicant: dict[str, Any],
        loan: dict[str, Any],
        materials: list[dict[str, Any]] | None = None,
        operator: str = "applicant",
    ) -> dict[str, Any]:
        """创建一笔新申请，写入审计日志，返回申请记录。"""

        now = time.time()
        application_id = self.repository.create_application(
            applicant=applicant,
            loan=loan,
            materials=materials or [],
            status=STATUS_SUBMITTED,
            created_at=now,
        )
        self.repository.add_audit(
            application_id,
            actor=operator,
            action="submit",
            detail=json.dumps(
                {"amount": loan.get("amount"), "materials": len(materials or [])},
                ensure_ascii=False,
            ),
            created_at=now,
        )
        record = self.get(application_id)
        assert record is not None
        return record

    # -- 查询 -----------------------------------------------------------------

    def get(self, application_id: int) -> dict[str, Any] | None:
        application = self.repository.get_application(application_id)
        if application is None:
            return None
        application["risk_assessments"] = self.repository.list_risk_assessments(
            application_id
        )
        application["approval_records"] = self.repository.list_approval_records(
            application_id
        )
        application["approval_chain"] = self.repository.list_chain(application_id)
        return application

    def list(
        self, *, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        return self.repository.list_applications(status=status, limit=limit)

    def audit_trail(self, application_id: int) -> dict[str, Any]:
        return {
            "application_id": application_id,
            "chain": self.repository.list_chain(application_id),
            "records": self.repository.list_approval_records(application_id),
            "audits": self.repository.list_audit(application_id),
        }

    # -- 落库一次工作流的结果 --------------------------------------------------

    def persist_run(self, application_id: int, state: dict[str, Any]) -> dict[str, Any]:
        """把一次工作流跑完后的状态写入数据库（风险评估 / 审批链 / 决策 / 审计）。

        幂等性：审批记录与审批链按"已存在的最大序号"追加，重复调用不会重复落。
        """

        now = time.time()
        assessment = state.get("risk_assessment") or {}
        if assessment:
            self.repository.save_risk_assessment(
                application_id, assessment=assessment, created_at=now
            )

        existing_records = len(self.repository.list_approval_records(application_id))
        for record in (state.get("approval_records") or [])[existing_records:]:
            self.repository.add_approval_record(
                application_id,
                approver=str(record.get("approver") or "system"),
                level=str(record.get("level") or "auto"),
                decision=str(record.get("decision") or ""),
                comment=str(record.get("comment") or ""),
                modified=bool(record.get("modified")),
                created_at=float(record.get("created_at") or now),
            )

        existing_chain = len(self.repository.list_chain(application_id))
        for entry in (state.get("approval_chain") or [])[existing_chain:]:
            self.repository.add_chain_entry(
                application_id,
                actor=str(entry.get("actor") or "system"),
                action=str(entry.get("action") or ""),
                from_level=str(entry.get("from_level") or ""),
                to_level=str(entry.get("to_level") or ""),
                detail=str(entry.get("detail") or ""),
                created_at=float(entry.get("created_at") or now),
            )

        decision = str(state.get("decision") or "")
        status = status_for_decision(decision) if decision else STATUS_PENDING
        self.repository.update_application(
            application_id,
            status=status,
            decision=decision or None,
            decision_reason=str(state.get("decision_reason") or ""),
            risk_level=str(state.get("risk_level") or "") or None,
            updated_at=now,
        )
        self.repository.add_audit(
            application_id,
            actor=str(state.get("operator_id") or "system"),
            action="finalize",
            detail=json.dumps(
                {
                    "decision": decision,
                    "status": status,
                    "route_level": state.get("route_level"),
                    "risk_level": state.get("risk_level"),
                },
                ensure_ascii=False,
            ),
            created_at=now,
        )
        record = self.get(application_id)
        assert record is not None
        return record

    # -- 流程事件 -------------------------------------------------------------

    def mark_in_review(self, application_id: int, *, stage: str) -> None:
        now = time.time()
        self.repository.update_application(
            application_id, status=STATUS_IN_REVIEW, updated_at=now
        )
        self.repository.add_audit(
            application_id,
            actor="system",
            action="stage",
            detail=stage,
            created_at=now,
        )

    def mark_interrupted(self, application_id: int, *, level: str) -> None:
        """工作流在人工审批处中断：记录一条"等待审批"的审计。"""

        now = time.time()
        self.repository.update_application(
            application_id, status=STATUS_PENDING, updated_at=now
        )
        self.repository.add_audit(
            application_id,
            actor="system",
            action="interrupt",
            detail=f"等待{LEVEL_LABELS.get(level, level)}审批",
            created_at=now,
        )

    def start_recheck(self, application_id: int, *, operator: str = "system") -> dict[str, Any]:
        """对已审批通过的贷款发起复审 / 抽查。"""

        now = time.time()
        self.repository.update_application(
            application_id,
            status=STATUS_IN_REVIEW,
            review_type="recheck",
            updated_at=now,
        )
        self.repository.add_audit(
            application_id,
            actor=operator,
            action="recheck_start",
            detail="发起贷后复审",
            created_at=now,
        )
        record = self.get(application_id)
        assert record is not None
        return record

    # -- 政策知识库 -----------------------------------------------------------

    def list_policy_libraries(self) -> list[dict[str, object]]:
        return self.knowledge.list_libraries() if self.knowledge else []

    def add_policy(self, library: str, title: str, content: str) -> int:
        if self.knowledge is None:
            raise RuntimeError("未配置政策知识库")
        return self.knowledge.add_document(library, title, content)


def is_terminal_status(status: str) -> bool:
    return status in _TERMINAL_STATUSES
