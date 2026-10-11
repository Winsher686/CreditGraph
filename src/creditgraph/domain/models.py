"""领域模型：跨层传递的纯数据结构。

这里只放"与框架无关"的数据容器——检索命中、切块，以及信贷申请相关的
轻量对象。数据库行、HTTP 报文都在各自层里转成这些结构后再进业务逻辑，
保证业务规则只依赖纯数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SearchHit:
    """一次检索命中的片段，带出处（用于风险评估的"依据"标注）。"""

    chunk_id: int
    library: str
    title: str
    content: str
    score: float


@dataclass(frozen=True)
class Chunk:
    """知识库（信贷政策 / 审批规则 / 风险案例）里的一个文本片段。"""

    id: int
    library: str
    title: str
    content: str


@dataclass(frozen=True)
class Applicant:
    """申请人（从提交材料里解析出的结构化信息）。"""

    name: str
    id_number: str = ""
    age: int = 0
    occupation: str = ""
    industry: str = ""
    monthly_income: float = 0.0
    monthly_debt: float = 0.0
    credit_score: int = 0
    blacklisted: bool = False
    related_party: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Applicant:
        data = data or {}
        return cls(
            name=str(data.get("name") or "未署名"),
            id_number=str(data.get("id_number") or ""),
            age=int(data.get("age") or 0),
            occupation=str(data.get("occupation") or ""),
            industry=str(data.get("industry") or ""),
            monthly_income=float(data.get("monthly_income") or 0.0),
            monthly_debt=float(data.get("monthly_debt") or 0.0),
            credit_score=int(data.get("credit_score") or 0),
            blacklisted=bool(data.get("blacklisted") or False),
            related_party=bool(data.get("related_party") or False),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "id_number": self.id_number,
            "age": self.age,
            "occupation": self.occupation,
            "industry": self.industry,
            "monthly_income": self.monthly_income,
            "monthly_debt": self.monthly_debt,
            "credit_score": self.credit_score,
            "blacklisted": self.blacklisted,
            "related_party": self.related_party,
        }


@dataclass(frozen=True)
class LoanTerms:
    """贷款要素。"""

    amount: float = 0.0
    term_months: int = 12
    purpose: str = ""
    annual_rate: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> LoanTerms:
        data = data or {}
        return cls(
            amount=float(data.get("amount") or 0.0),
            term_months=int(data.get("term_months") or 12),
            purpose=str(data.get("purpose") or ""),
            annual_rate=float(data.get("annual_rate") or 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount": self.amount,
            "term_months": self.term_months,
            "purpose": self.purpose,
            "annual_rate": self.annual_rate,
        }


@dataclass(frozen=True)
class RiskAssessment:
    """AI 生成的初步风险评估报告。"""

    credit_score: int = 0
    debt_to_income: float = 0.0
    risk_level: str = "medium"
    repayment_capacity: str = ""
    summary: str = ""
    risk_factors: tuple[str, ...] = field(default_factory=tuple)
    recommendation: str = ""
    citations: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "credit_score": self.credit_score,
            "debt_to_income": self.debt_to_income,
            "risk_level": self.risk_level,
            "repayment_capacity": self.repayment_capacity,
            "summary": self.summary,
            "risk_factors": list(self.risk_factors),
            "recommendation": self.recommendation,
            "citations": list(self.citations),
        }


@dataclass(frozen=True)
class ApprovalRecord:
    """一条审批记录（某一级审批人的操作）。"""

    approver: str
    level: str
    decision: str
    comment: str = ""
    modified: bool = False
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "approver": self.approver,
            "level": self.level,
            "decision": self.decision,
            "comment": self.comment,
            "modified": self.modified,
            "created_at": self.created_at,
        }
