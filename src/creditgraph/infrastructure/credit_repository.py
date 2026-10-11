"""信贷审批的 SQLite 实现（`CreditRepositoryPort`）。

六张业务表：
- `applications`      申请表：申请人信息、金额、期限、用途、状态、提交时间；
- `risk_assessments`  风险评估表：AI 评分、还款能力、风险等级、生成时间；
- `approval_records`  审批记录表：审批人、审批层级、决策、意见、时间；
- `approval_chain`    审批链表：每一级审批的流转关系（from → to）；
- `audit_logs`        审计日志表：所有操作记录，支持合规审计。

知识库表（政策 / 规则 / 案例）由 `knowledge.py` 负责，与本文件共用一个库文件。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    applicant_json TEXT NOT NULL,
    loan_json TEXT NOT NULL,
    materials_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    decision TEXT,
    decision_reason TEXT,
    risk_level TEXT,
    review_type TEXT NOT NULL DEFAULT 'new',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status, created_at DESC);

CREATE TABLE IF NOT EXISTS risk_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL,
    credit_score INTEGER NOT NULL DEFAULT 0,
    debt_to_income REAL NOT NULL DEFAULT 0,
    risk_level TEXT NOT NULL DEFAULT 'medium',
    repayment_capacity TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    recommendation TEXT NOT NULL DEFAULT '',
    risk_factors TEXT NOT NULL DEFAULT '[]',
    citations TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_risk_application ON risk_assessments(application_id, id);

CREATE TABLE IF NOT EXISTS approval_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL,
    approver TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    modified INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_application ON approval_records(application_id, id);

CREATE TABLE IF NOT EXISTS approval_chain (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL,
    seq INTEGER NOT NULL DEFAULT 0,
    actor TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    from_level TEXT NOT NULL DEFAULT '',
    to_level TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chain_application ON approval_chain(application_id, seq);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL,
    actor TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_application ON audit_logs(application_id, id);
"""


def _loads(raw: Any, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


class SqliteCreditRepository:
    """`CreditRepositoryPort` 的 SQLite 实现。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    # -- 申请 -----------------------------------------------------------------

    def create_application(
        self,
        *,
        applicant: dict[str, Any],
        loan: dict[str, Any],
        materials: list[dict[str, Any]] | None = None,
        status: str,
        created_at: float,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO applications(applicant_json, loan_json, materials_json, "
                "status, review_type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    json.dumps(applicant, ensure_ascii=False),
                    json.dumps(loan, ensure_ascii=False),
                    json.dumps(materials or [], ensure_ascii=False),
                    status,
                    "new",
                    created_at,
                    created_at,
                ),
            )
            return int(cursor.lastrowid)

    def get_application(self, application_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM applications WHERE id = ?", (application_id,)
            ).fetchone()
        return self._application_row(row) if row else None

    def list_applications(
        self, *, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM applications"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(int(limit))
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._application_row(row) for row in rows]

    def update_application(
        self,
        application_id: int,
        *,
        status: str | None = None,
        decision: str | None = None,
        decision_reason: str | None = None,
        risk_level: str | None = None,
        review_type: str | None = None,
        updated_at: float,
    ) -> bool:
        fields = ["updated_at = ?"]
        params: list[Any] = [updated_at]
        for column, value in (
            ("status", status),
            ("decision", decision),
            ("decision_reason", decision_reason),
            ("risk_level", risk_level),
            ("review_type", review_type),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                params.append(value)
        params.append(application_id)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE applications SET {', '.join(fields)} WHERE id = ?", params
            )
            return cursor.rowcount > 0

    @staticmethod
    def _application_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "applicant": _loads(row["applicant_json"], {}),
            "loan": _loads(row["loan_json"], {}),
            "materials": _loads(row["materials_json"], []),
            "status": str(row["status"]),
            "decision": row["decision"],
            "decision_reason": row["decision_reason"] or "",
            "risk_level": row["risk_level"],
            "review_type": str(row["review_type"] or "new"),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }

    # -- 风险评估 --------------------------------------------------------------

    def save_risk_assessment(
        self, application_id: int, *, assessment: dict[str, Any], created_at: float
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO risk_assessments(application_id, credit_score, "
                "debt_to_income, risk_level, repayment_capacity, summary, "
                "recommendation, risk_factors, citations, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    application_id,
                    int(assessment.get("credit_score") or 0),
                    float(assessment.get("debt_to_income") or 0.0),
                    str(assessment.get("risk_level") or "medium"),
                    str(assessment.get("repayment_capacity") or ""),
                    str(assessment.get("summary") or ""),
                    str(assessment.get("recommendation") or ""),
                    json.dumps(assessment.get("risk_factors") or [], ensure_ascii=False),
                    json.dumps(assessment.get("citations") or [], ensure_ascii=False),
                    created_at,
                ),
            )
            return int(cursor.lastrowid)

    def list_risk_assessments(self, application_id: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM risk_assessments WHERE application_id = ? ORDER BY id",
                (application_id,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "application_id": int(row["application_id"]),
                "credit_score": int(row["credit_score"]),
                "debt_to_income": float(row["debt_to_income"]),
                "risk_level": str(row["risk_level"]),
                "repayment_capacity": str(row["repayment_capacity"]),
                "summary": str(row["summary"]),
                "recommendation": str(row["recommendation"]),
                "risk_factors": _loads(row["risk_factors"], []),
                "citations": _loads(row["citations"], []),
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]

    # -- 审批记录 / 审批链 / 审计 ---------------------------------------------

    def add_approval_record(
        self,
        application_id: int,
        *,
        approver: str,
        level: str,
        decision: str,
        comment: str,
        modified: bool,
        created_at: float,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO approval_records(application_id, approver, level, "
                "decision, comment, modified, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    application_id,
                    approver,
                    level,
                    decision,
                    comment,
                    1 if modified else 0,
                    created_at,
                ),
            )
            return int(cursor.lastrowid)

    def list_approval_records(self, application_id: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM approval_records WHERE application_id = ? ORDER BY id",
                (application_id,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "application_id": int(row["application_id"]),
                "approver": str(row["approver"]),
                "level": str(row["level"]),
                "decision": str(row["decision"]),
                "comment": str(row["comment"]),
                "modified": bool(row["modified"]),
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]

    def add_chain_entry(
        self,
        application_id: int,
        *,
        actor: str,
        action: str,
        from_level: str,
        to_level: str,
        detail: str,
        created_at: float,
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM approval_chain "
                "WHERE application_id = ?",
                (application_id,),
            ).fetchone()
            seq = int(row["max_seq"]) + 1
            cursor = connection.execute(
                "INSERT INTO approval_chain(application_id, seq, actor, action, "
                "from_level, to_level, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    application_id,
                    seq,
                    actor,
                    action,
                    from_level,
                    to_level,
                    detail,
                    created_at,
                ),
            )
            return int(cursor.lastrowid)

    def list_chain(self, application_id: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM approval_chain WHERE application_id = ? ORDER BY seq, id",
                (application_id,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "seq": int(row["seq"]),
                "actor": str(row["actor"]),
                "action": str(row["action"]),
                "from_level": str(row["from_level"]),
                "to_level": str(row["to_level"]),
                "detail": str(row["detail"]),
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]

    def add_audit(
        self,
        application_id: int,
        *,
        actor: str,
        action: str,
        detail: str,
        created_at: float,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO audit_logs(application_id, actor, action, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (application_id, actor, action, detail, created_at),
            )
            return int(cursor.lastrowid)

    def list_audit(
        self, application_id: int, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_logs WHERE application_id = ? ORDER BY id LIMIT ?",
                (application_id, int(limit)),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "application_id": int(row["application_id"]),
                "actor": str(row["actor"]),
                "action": str(row["action"]),
                "detail": str(row["detail"]),
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]


def now() -> float:
    return time.time()
