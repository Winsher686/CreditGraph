from __future__ import annotations

from creditgraph.domain.credit import (
    DECISION_AUTO_APPROVED,
    DECISION_AUTO_REJECTED,
    DECISION_PENDING,
    HIGH,
    LEVEL_AUTO,
    LEVEL_COMPLIANCE,
    LEVEL_JUNIOR,
    LEVEL_SENIOR,
    LOW,
    MEDIUM,
    CreditRules,
    RuleResult,
    approval_matrix_rows,
    evaluate_rules,
    next_approval_level,
    route_approval,
)

RULES = CreditRules()


def test_auto_reject_routes_to_terminal_rejection() -> None:
    result = RuleResult(auto_reject=True, reject_reasons=("征信黑名单",))
    route = route_approval(HIGH, 500_000, result, RULES)
    assert route.level == LEVEL_AUTO
    assert route.decision == DECISION_AUTO_REJECTED


def test_auto_approve_routes_to_terminal_approval() -> None:
    result = RuleResult(auto_approve=True, approve_reasons=("低风险小额",))
    route = route_approval(LOW, 60_000, result, RULES)
    assert route.level == LEVEL_AUTO
    assert route.decision == DECISION_AUTO_APPROVED


def test_low_risk_small_amount_auto() -> None:
    applicant = {
        "monthly_income": 25000,
        "monthly_debt": 2000,
        "credit_score": 760,
        "industry": "信息技术",
        "related_party": False,
        "blacklisted": False,
    }
    loan = {"amount": 60_000, "term_months": 24, "purpose": "家电购置"}
    rule_result = evaluate_rules(applicant, loan, LOW, RULES)
    route = route_approval(LOW, 60_000, rule_result, RULES)
    assert route.level == LEVEL_AUTO
    assert route.decision == DECISION_AUTO_APPROVED


def test_medium_risk_mid_amount_to_junior_reviewer() -> None:
    route = route_approval(MEDIUM, 300_000, RuleResult(manual_review=True), RULES)
    assert route.level == LEVEL_JUNIOR
    assert route.decision == DECISION_PENDING


def test_high_risk_to_senior_reviewer() -> None:
    route = route_approval(HIGH, 80_000, RuleResult(manual_review=True), RULES)
    assert route.level == LEVEL_SENIOR


def test_large_amount_to_senior_reviewer() -> None:
    route = route_approval(MEDIUM, 600_000, RuleResult(manual_review=True), RULES)
    assert route.level == LEVEL_SENIOR


def test_compliance_takes_priority_over_senior() -> None:
    result = RuleResult(requires_compliance=True, compliance_reasons=("反洗钱",))
    route = route_approval(HIGH, 3_000_000, result, RULES)
    assert route.level == LEVEL_COMPLIANCE


def test_escalation_chain_junior_senior_compliance() -> None:
    assert next_approval_level(LEVEL_JUNIOR) == LEVEL_SENIOR
    assert next_approval_level(LEVEL_SENIOR) == LEVEL_COMPLIANCE
    assert next_approval_level(LEVEL_COMPLIANCE) is None


def test_approval_matrix_rows_render_thresholds() -> None:
    rows = approval_matrix_rows(RULES)
    assert len(rows) == 4
    levels = {row["level"] for row in rows}
    assert levels == {LEVEL_AUTO, LEVEL_JUNIOR, LEVEL_SENIOR, LEVEL_COMPLIANCE}
    assert any("100,000" in row["condition"] for row in rows)
