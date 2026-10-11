from __future__ import annotations

import time
from pathlib import Path

from creditgraph.application.credit_service import (
    STATUS_APPROVED,
    STATUS_PENDING,
    CreditService,
)
from creditgraph.infrastructure.credit_repository import SqliteCreditRepository


def _service(tmp_path: Path) -> CreditService:
    repository = SqliteCreditRepository(tmp_path / "audit.db")
    return CreditService(repository, None)


def test_submit_creates_application_and_audit(tmp_path: Path) -> None:
    service = _service(tmp_path)
    record = service.submit(
        applicant={"name": "张三", "monthly_income": 15000},
        loan={"amount": 200000, "term_months": 24, "purpose": "装修"},
        materials=[{"name": "收入证明", "text": "月收入 15000 元"}],
        operator="applicant",
    )
    assert record["id"] >= 1
    assert record["status"] == "submitted"
    assert record["materials"][0]["name"] == "收入证明"

    trail = service.audit_trail(record["id"])
    assert any(log["action"] == "submit" for log in trail["audits"])


def test_persist_run_writes_chain_and_is_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    record = service.submit(
        applicant={"name": "李四"},
        loan={"amount": 300000, "term_months": 36, "purpose": "经营"},
        materials=[],
        operator="applicant",
    )
    application_id = record["id"]
    now = time.time()
    state = {
        "risk_assessment": {"credit_score": 660, "risk_level": "medium", "summary": "中风险"},
        "risk_level": "medium",
        "route_level": "junior",
        "operator_id": "审批员甲",
        "decision": "approved",
        "decision_reason": "同意",
        "approval_records": [
            {
                "approver": "审批员甲",
                "level": "junior",
                "decision": "approve",
                "comment": "同意",
                "modified": False,
                "created_at": now,
            }
        ],
        "approval_chain": [
            {
                "actor": "system",
                "action": "route",
                "from_level": "auto",
                "to_level": "junior",
                "detail": "转初级审批",
                "created_at": now,
            },
            {
                "actor": "审批员甲",
                "action": "approve",
                "from_level": "junior",
                "to_level": "approved",
                "detail": "同意",
                "created_at": now,
            },
        ],
    }

    first = service.persist_run(application_id, state)
    assert first["status"] == STATUS_APPROVED
    assert first["decision"] == "approved"
    assert len(first["approval_chain"]) == 2
    assert len(first["approval_records"]) == 1
    assert first["risk_assessments"][-1]["risk_level"] == "medium"

    # 再次落库不应重复追加
    second = service.persist_run(application_id, state)
    assert len(second["approval_chain"]) == 2
    assert len(second["approval_records"]) == 1


def test_list_applications_filters_by_status(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.submit(
        applicant={"name": "甲"}, loan={"amount": 10000}, materials=[], operator="a"
    )
    service.submit(
        applicant={"name": "乙"}, loan={"amount": 20000}, materials=[], operator="a"
    )
    all_rows = service.list()
    assert len(all_rows) == 2
    assert service.list(status=STATUS_PENDING) == []


def test_start_recheck_sets_review_type(tmp_path: Path) -> None:
    service = _service(tmp_path)
    record = service.submit(
        applicant={"name": "王五"},
        loan={"amount": 100000},
        materials=[],
        operator="a",
    )
    application_id = record["id"]
    service.persist_run(
        application_id,
        {"decision": "approved", "risk_level": "low", "operator_id": "甲"},
    )
    service.start_recheck(application_id, operator="风控")
    updated = service.get(application_id)
    assert updated is not None
    assert updated["review_type"] == "recheck"
    assert any(
        log["action"] == "recheck_start" for log in service.audit_trail(application_id)["audits"]
    )


def test_get_missing_application_returns_none(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert service.get(999) is None
