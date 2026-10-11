from __future__ import annotations

from creditgraph.domain.credit import (
    HIGH,
    LOW,
    MEDIUM,
    CreditRules,
    debt_to_income,
    evaluate_rules,
    monthly_payment,
)

RULES = CreditRules()


def _applicant(**overrides):
    base = {
        "name": "测试",
        "monthly_income": 20000.0,
        "monthly_debt": 4000.0,
        "credit_score": 700,
        "industry": "信息技术",
        "related_party": False,
        "blacklisted": False,
    }
    base.update(overrides)
    return base


def _loan(**overrides):
    base = {"amount": 80000.0, "term_months": 24, "purpose": "装修", "annual_rate": 0.049}
    base.update(overrides)
    return base


def test_debt_to_income_and_monthly_payment() -> None:
    assert debt_to_income(20000, 4000) == 0.2
    assert debt_to_income(0, 100) > 1.0
    # 等额本息月供应为正且小于本金总额
    pay = monthly_payment(100000, 24, 0.049)
    assert 0 < pay < 100000
    # 零利率退化为本金 / 期数
    assert monthly_payment(24000, 24, 0.0) == 1000.0


def test_auto_reject_on_blacklist() -> None:
    result = evaluate_rules(_applicant(blacklisted=True), _loan(), LOW, RULES)
    assert result.auto_reject is True
    assert any("黑名单" in reason for reason in result.reject_reasons)


def test_auto_reject_on_low_income_and_high_dti() -> None:
    low_income = evaluate_rules(_applicant(monthly_income=1000), _loan(), LOW, RULES)
    assert low_income.auto_reject is True

    high_dti = evaluate_rules(
        _applicant(monthly_income=10000, monthly_debt=9000), _loan(), LOW, RULES
    )
    assert high_dti.auto_reject is True
    assert any("负债率" in reason for reason in high_dti.reject_reasons)


def test_auto_approve_for_low_risk_small_amount() -> None:
    result = evaluate_rules(_applicant(credit_score=780), _loan(amount=60000), LOW, RULES)
    assert result.auto_approve is True
    assert result.auto_reject is False


def test_compliance_triggered_by_aml_amount_purpose_and_industry() -> None:
    big = evaluate_rules(_applicant(), _loan(amount=3_000_000), MEDIUM, RULES)
    assert big.requires_compliance is True

    suspicious = evaluate_rules(_applicant(), _loan(purpose="现金周转"), MEDIUM, RULES)
    assert suspicious.requires_compliance is True

    restricted = evaluate_rules(
        _applicant(industry="房地产开发"), _loan(), MEDIUM, RULES
    )
    assert restricted.requires_compliance is True

    related = evaluate_rules(_applicant(related_party=True), _loan(), MEDIUM, RULES)
    assert related.requires_compliance is True


def test_auto_approve_blocked_when_compliance_triggered() -> None:
    """合规命中优先于自动通过——不得出现"黑钱走自动放款"。"""

    result = evaluate_rules(
        _applicant(credit_score=780), _loan(amount=60_000, purpose="代他人借款"), LOW, RULES
    )
    assert result.auto_approve is False
    assert result.requires_compliance is True


def test_manual_review_default() -> None:
    result = evaluate_rules(_applicant(), _loan(amount=300_000), MEDIUM, RULES)
    assert result.auto_reject is False
    assert result.auto_approve is False
    assert result.requires_compliance is False
    assert result.manual_review is True


def test_rule_result_roundtrip() -> None:
    from creditgraph.domain.credit import RuleResult

    original = evaluate_rules(_applicant(), _loan(), HIGH, RULES)
    restored = RuleResult.from_dict(original.to_dict())
    assert restored == original


def test_rules_are_configurable() -> None:
    strict = CreditRules(auto_approve_max_amount=50_000, min_credit_score=700)
    # 金额 6 万在默认规则下可自动通过，严格规则下不能
    assert evaluate_rules(
        _applicant(credit_score=720), _loan(amount=60_000), LOW, RULES
    ).auto_approve
    assert not evaluate_rules(
        _applicant(credit_score=720), _loan(amount=60_000), LOW, strict
    ).auto_approve
