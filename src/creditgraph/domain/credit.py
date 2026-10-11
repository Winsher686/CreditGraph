"""信贷审批的领域规则（**纯逻辑**，零依赖、可单测）。

这里放"信贷这件事本身"的规则，与 LangGraph / SQLite / FastAPI 无关：

- **风险等级 / 决策 / 审批层级**的枚举与中文标签；
- **规则引擎**：自动拒绝（黑名单 / 收入不达标 / 负债率过高）、自动通过
  （低风险 + 小额 + 优质客户）、转人工、合规检查（反洗钱 / 关联交易 /
  行业限制）——全部为确定性纯函数；
- **审批矩阵**：`风险等级 × 金额 × 反洗钱` → 审批层级；
- **升级机制**：初级审批人无法决定时逐级升级；
- **超时升级** / 还款能力（月供、负债率）等工具函数。

所有阈值由 `CreditRules` 注入（来自配置文件 / 环境变量 / 数据库），
因此"规则可配置"是天然成立的：换一份 `CreditRules` 就换一套审批策略。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

# ---------------------------------------------------------------------------
# 枚举与标签
# ---------------------------------------------------------------------------

LOW = "low"
MEDIUM = "medium"
HIGH = "high"
RISK_LEVELS = (LOW, MEDIUM, HIGH)
RISK_LABELS = {LOW: "低风险", MEDIUM: "中风险", HIGH: "高风险"}

# 决策：终局态 + 流程态
DECISION_PENDING = "pending_review"       # 待人工审批（流程中）
DECISION_AUTO_APPROVED = "auto_approved"  # 规则引擎自动通过
DECISION_AUTO_REJECTED = "auto_rejected"  # 规则引擎自动拒绝
DECISION_APPROVED = "approved"            # 人工审批通过
DECISION_REJECTED = "rejected"            # 人工审批拒绝
DECISION_CONDITIONAL = "conditional"      # 有条件通过
DECISION_ESCALATED = "escalated"          # 已升级

# 审批层级
LEVEL_AUTO = "auto"
LEVEL_JUNIOR = "junior"
LEVEL_SENIOR = "senior"
LEVEL_COMPLIANCE = "compliance"
ALL_LEVELS = (LEVEL_AUTO, LEVEL_JUNIOR, LEVEL_SENIOR, LEVEL_COMPLIANCE)
LEVEL_LABELS = {
    LEVEL_AUTO: "自动审批",
    LEVEL_JUNIOR: "初级审批人",
    LEVEL_SENIOR: "高级审批人",
    LEVEL_COMPLIANCE: "合规部门",
}

# 升级顺序：初级 → 高级 → 合规
_ESCALATION_CHAIN = (LEVEL_JUNIOR, LEVEL_SENIOR, LEVEL_COMPLIANCE)


# ---------------------------------------------------------------------------
# 规则配置
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreditRules:
    """可配置的信贷规则阈值。默认值即"推荐策略"，可通过 .env / 数据库覆盖。"""

    # 审批矩阵金额阈值（元）
    auto_approve_max_amount: float = 100_000.0     # 低风险 + 金额 < 10 万 → 自动通过
    senior_review_min_amount: float = 500_000.0    # 金额 > 50 万 → 高级审批人
    # 反洗钱触发金额（元）
    aml_amount_threshold: float = 2_000_000.0
    # 自动拒绝阈值
    max_dti: float = 0.70               # 负债率（月负债/月收入）上限
    min_monthly_income: float = 3_000.0  # 最低月收入要求
    min_credit_score: int = 600          # 自动通过的最低信用评分
    auto_approve_max_dti: float = 0.50   # 自动通过要求负债率低于此值
    # 风险等级判定（评分越高越安全）
    high_risk_score_below: int = 620
    low_risk_score_at_least: int = 720
    # 超时（分钟）：超过则提醒 / 升级
    approval_timeout_minutes: int = 120
    # 合规检查：受限行业与可疑用途
    restricted_industries: tuple[str, ...] = (
        "虚拟货币", "博彩", "典当", "民间借贷", "房地产开发",
    )
    aml_purposes: tuple[str, ...] = (
        "现金周转", "代他人借款", "跨境转账", "无明确用途",
    )

    def merged(self, overrides: dict[str, Any] | None) -> CreditRules:
        """用外部覆盖值生成新规则（非法键忽略，保证向前兼容）。"""

        if not overrides:
            return self
        allowed = {f for f in self.__dataclass_fields__}
        clean = {k: v for k, v in overrides.items() if k in allowed and v is not None}
        for key in ("restricted_industries", "aml_purposes"):
            if key in clean and isinstance(clean[key], list):
                clean[key] = tuple(clean[key])
        return replace(self, **clean) if clean else self


# ---------------------------------------------------------------------------
# 纯计算工具
# ---------------------------------------------------------------------------


def debt_to_income(monthly_income: float, monthly_debt: float) -> float:
    """负债率 = 月负债 / 月收入。收入为 0 时视为极高负债（返回 1.0 以上）。"""

    if monthly_income <= 0:
        return 9.99
    return round(monthly_debt / monthly_income, 4)


def monthly_payment(amount: float, term_months: int, annual_rate: float) -> float:
    """等额本息月供。利率为 0 时退化为本金 / 期数。"""

    n = max(1, int(term_months))
    if annual_rate <= 0:
        return round(amount / n, 2)
    monthly_rate = annual_rate / 12.0
    factor = (1 + monthly_rate) ** n
    return round(amount * monthly_rate * factor / (factor - 1), 2)


def _risk_level_from_score(credit_score: int, dti: float, rules: CreditRules) -> str:
    """按评分 + 负债率给出风险等级（确定性回退口径）。"""

    if credit_score and credit_score < rules.high_risk_score_below:
        return HIGH
    if dti > rules.max_dti:
        return HIGH
    if credit_score >= rules.low_risk_score_at_least and dti <= rules.auto_approve_max_dti:
        return LOW
    return MEDIUM


def heuristic_risk(
    applicant: Any, loan: Any, rules: CreditRules
) -> dict[str, Any]:
    """确定性风险评估（在模型不可用 / 输出不可解析时兜底）。

    `applicant` / `loan` 既可以是 `Applicant` / `LoanTerms` 对象，也可以是 dict。
    """

    get = (lambda k: applicant.get(k)) if isinstance(applicant, dict) else (
        lambda k: getattr(applicant, k, None)
    )
    lget = (lambda k: loan.get(k)) if isinstance(loan, dict) else (
        lambda k: getattr(loan, k, None)
    )

    income = float(get("monthly_income") or 0.0)
    debt = float(get("monthly_debt") or 0.0)
    score = int(get("credit_score") or 0)
    amount = float(lget("amount") or 0.0)
    term = int(lget("term_months") or 12)
    rate = float(lget("annual_rate") or 0.0)
    blacklisted = bool(get("blacklisted"))

    dti = debt_to_income(income, debt)
    pay = monthly_payment(amount, term, rate)
    level = HIGH if blacklisted else _risk_level_from_score(score, dti, rules)

    factors: list[str] = []
    if blacklisted:
        factors.append("申请人命中征信黑名单")
    if income < rules.min_monthly_income:
        factors.append("月收入低于最低要求")
    if dti > rules.max_dti:
        factors.append(f"负债率 {dti:.0%} 超过上限 {rules.max_dti:.0%}")
    if amount >= rules.senior_review_min_amount:
        factors.append("申请金额达到高级审批线")

    capacity = (
        f"月供约 {pay:.0f} 元，占月收入约 {(pay / income if income else 1):.0%}；"
        f"负债率 {dti:.0%}"
    )
    return {
        "credit_score": score,
        "debt_to_income": dti,
        "monthly_payment": pay,
        "risk_level": level,
        "repayment_capacity": capacity,
        "summary": f"综合信用评分 {score}、负债率 {dti:.0%}，判定为{RISK_LABELS[level]}。",
        "risk_factors": factors,
        "recommendation": {
            HIGH: "建议拒绝或要求补充担保 / 降低额度",
            MEDIUM: "建议转人工审批，核实收入与负债",
            LOW: "建议按标准流程放款",
        }[level],
    }


# ---------------------------------------------------------------------------
# 规则引擎
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleResult:
    """规则引擎的输出：决定"能不能自动终局、要不要转人工/合规"。"""

    auto_reject: bool = False
    auto_approve: bool = False
    requires_compliance: bool = False
    manual_review: bool = False
    reject_reasons: tuple[str, ...] = field(default_factory=tuple)
    approve_reasons: tuple[str, ...] = field(default_factory=tuple)
    compliance_reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def reasons(self) -> tuple[str, ...]:
        return self.reject_reasons + self.approve_reasons + self.compliance_reasons

    def to_dict(self) -> dict[str, Any]:
        return {
            "auto_reject": self.auto_reject,
            "auto_approve": self.auto_approve,
            "requires_compliance": self.requires_compliance,
            "manual_review": self.manual_review,
            "reject_reasons": list(self.reject_reasons),
            "approve_reasons": list(self.approve_reasons),
            "compliance_reasons": list(self.compliance_reasons),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RuleResult:
        data = data or {}
        return cls(
            auto_reject=bool(data.get("auto_reject")),
            auto_approve=bool(data.get("auto_approve")),
            requires_compliance=bool(data.get("requires_compliance")),
            manual_review=bool(data.get("manual_review")),
            reject_reasons=tuple(data.get("reject_reasons") or ()),
            approve_reasons=tuple(data.get("approve_reasons") or ()),
            compliance_reasons=tuple(data.get("compliance_reasons") or ()),
        )


def evaluate_rules(
    applicant: Any,
    loan: Any,
    risk_level: str,
    rules: CreditRules,
) -> RuleResult:
    """跑一遍内置信贷规则。

    优先级：**自动拒绝 > 合规拦截 > 自动通过 > 转人工**。任何一条自动拒绝
    命中即终局拒绝（合规与自动通过都不再看），避免"黑名单客户被自动放款"。
    """

    get = (lambda k: applicant.get(k)) if isinstance(applicant, dict) else (
        lambda k: getattr(applicant, k, None)
    )
    lget = (lambda k: loan.get(k)) if isinstance(loan, dict) else (
        lambda k: getattr(loan, k, None)
    )

    income = float(get("monthly_income") or 0.0)
    debt = float(get("monthly_debt") or 0.0)
    score = int(get("credit_score") or 0)
    amount = float(lget("amount") or 0.0)
    purpose = str(lget("purpose") or "")
    industry = str(get("industry") or "")
    blacklisted = bool(get("blacklisted"))
    related_party = bool(get("related_party"))
    dti = debt_to_income(income, debt)

    # --- 自动拒绝 ---------------------------------------------------------
    reject: list[str] = []
    if blacklisted:
        reject.append("申请人命中征信黑名单")
    if income < rules.min_monthly_income:
        reject.append(f"月收入 {income:.0f} 低于门槛 {rules.min_monthly_income:.0f}")
    if dti > rules.max_dti:
        reject.append(f"负债率 {dti:.0%} 超过上限 {rules.max_dti:.0%}")
    if reject:
        return RuleResult(auto_reject=True, reject_reasons=tuple(reject))

    # --- 合规检查（反洗钱 / 关联交易 / 行业限制） --------------------------
    compliance: list[str] = []
    if amount >= rules.aml_amount_threshold:
        compliance.append(f"金额 {amount:.0f} 达到反洗钱上报线")
    if purpose and purpose in rules.aml_purposes:
        compliance.append(f"用途「{purpose}」触发反洗钱可疑交易")
    if industry and industry in rules.restricted_industries:
        compliance.append(f"行业「{industry}」属于受限行业")
    if related_party:
        compliance.append("存在关联交易，需合规审查")

    # --- 自动通过 ---------------------------------------------------------
    approve: list[str] = []
    if (
        not compliance
        and risk_level == LOW
        and amount < rules.auto_approve_max_amount
        and score >= rules.min_credit_score
        and dti <= rules.auto_approve_max_dti
    ):
        approve.append(
            f"低风险且金额 {amount:.0f} 低于自动通过线 {rules.auto_approve_max_amount:.0f}"
        )

    if approve:
        return RuleResult(auto_approve=True, approve_reasons=tuple(approve))

    # --- 转人工 -----------------------------------------------------------
    manual = True
    if compliance:
        return RuleResult(
            requires_compliance=True,
            manual_review=True,
            compliance_reasons=tuple(compliance),
        )
    return RuleResult(manual_review=manual)


# ---------------------------------------------------------------------------
# 审批矩阵与路由
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteResult:
    """审批路由结果。"""

    level: str
    decision: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "level_label": LEVEL_LABELS.get(self.level, self.level),
            "decision": self.decision,
            "reason": self.reason,
        }


def route_approval(
    risk_level: str, amount: float, rule_result: RuleResult, rules: CreditRules
) -> RouteResult:
    """审批矩阵：把规则结果 + 风险等级 + 金额，路由到审批层级 / 终局决策。

    规则：
    - 自动拒绝命中 → 终局拒绝；
    - 自动通过命中 → 终局通过；
    - 触发反洗钱 / 关联交易 / 行业限制 → **合规部门**；
    - 高风险 **或** 金额 > 50 万 → **高级审批人**；
    - 其余（中风险 / 10–50 万）→ **初级审批人**。
    """

    if rule_result.auto_reject:
        return RouteResult(
            level=LEVEL_AUTO,
            decision=DECISION_AUTO_REJECTED,
            reason="；".join(rule_result.reject_reasons) or "命中自动拒绝规则",
        )
    if rule_result.auto_approve:
        return RouteResult(
            level=LEVEL_AUTO,
            decision=DECISION_AUTO_APPROVED,
            reason="；".join(rule_result.approve_reasons) or "命中自动通过规则",
        )
    if rule_result.requires_compliance:
        return RouteResult(
            level=LEVEL_COMPLIANCE,
            decision=DECISION_PENDING,
            reason="；".join(rule_result.compliance_reasons) or "需合规审查",
        )
    if risk_level == HIGH or amount >= rules.senior_review_min_amount:
        detail = []
        if risk_level == HIGH:
            detail.append(f"风险等级为{RISK_LABELS[HIGH]}")
        if amount >= rules.senior_review_min_amount:
            detail.append(
                f"金额 {amount:.0f} 超过高级审批线 {rules.senior_review_min_amount:.0f}"
            )
        return RouteResult(
            level=LEVEL_SENIOR,
            decision=DECISION_PENDING,
            reason="；".join(detail),
        )
    return RouteResult(
        level=LEVEL_JUNIOR,
        decision=DECISION_PENDING,
        reason=f"中风险 / 金额 {amount:.0f} 落在初级审批区间",
    )


def next_approval_level(current: str) -> str | None:
    """升级机制：返回上一级审批层级；已在最高级返回 None。"""

    if current in _ESCALATION_CHAIN:
        index = _ESCALATION_CHAIN.index(current)
        if index + 1 < len(_ESCALATION_CHAIN):
            return _ESCALATION_CHAIN[index + 1]
    if current == LEVEL_AUTO:
        return LEVEL_JUNIOR
    return None


def approval_matrix_rows(rules: CreditRules) -> list[dict[str, str]]:
    """把审批矩阵渲染成"人看得懂"的表格行（文档 / 提示词 / 前端共用）。"""

    return [
        {
            "condition": f"低风险 且 金额 < {rules.auto_approve_max_amount:,.0f}",
            "route": "自动通过",
            "level": LEVEL_AUTO,
        },
        {
            "condition": f"中风险 且 金额 {rules.auto_approve_max_amount:,.0f}–"
            f"{rules.senior_review_min_amount:,.0f}",
            "route": "初级审批人确认",
            "level": LEVEL_JUNIOR,
        },
        {
            "condition": f"高风险 或 金额 > {rules.senior_review_min_amount:,.0f}",
            "route": "高级审批人审批",
            "level": LEVEL_SENIOR,
        },
        {
            "condition": "触发反洗钱 / 关联交易 / 行业限制",
            "route": "合规部门审批",
            "level": LEVEL_COMPLIANCE,
        },
    ]


def summarize_rules(rules: CreditRules) -> str:
    """一段中文规则说明，注入提示词或写入 README。"""

    rows = approval_matrix_rows(rules)
    matrix = "\n".join(f"- {row['condition']} → {row['route']}" for row in rows)
    return (
        "【内置信贷规则】\n"
        "自动拒绝：征信黑名单 / 月收入低于 "
        f"{rules.min_monthly_income:,.0f} 元 / 负债率高于 {rules.max_dti:.0%}。\n"
        "自动通过：低风险、金额低于 "
        f"{rules.auto_approve_max_amount:,.0f} 元、信用评分不低于 "
        f"{rules.min_credit_score}、负债率不高于 {rules.auto_approve_max_dti:.0%}。\n"
        "合规检查：金额不低于 "
        f"{rules.aml_amount_threshold:,.0f} 元、可疑用途、受限行业或关联交易。\n"
        "审批矩阵：\n" + matrix
    )
