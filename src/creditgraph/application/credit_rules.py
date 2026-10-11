"""规则引擎编排：把可配置阈值与领域纯函数组装成审批可用的规则服务。

领域层（`domain/credit.py`）提供确定性的判定逻辑；这里只负责：
- 从配置 / 数据库装载阈值（`load_rules`）；
- 对外暴露"评估 + 路由"的用例入口；
- 渲染规则说明（注入提示词 / 写入接口返回，供审批人核对）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Settings
from ..domain.credit import (
    CreditRules,
    RouteResult,
    RuleResult,
    approval_matrix_rows,
    evaluate_rules,
    route_approval,
    summarize_rules,
)


def load_rules(
    settings: Settings | None = None, *, rules_file: str | None = None
) -> CreditRules:
    """装载规则：默认值 ← 配置文件（可选） ← 环境变量覆盖。"""

    rules = CreditRules()
    if rules_file:
        path = Path(rules_file)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    rules = rules.merged(data)
            except (json.JSONDecodeError, OSError):
                pass
    if settings is not None:
        rules = rules.merged(settings.rule_overrides())
    return rules


def evaluate(
    applicant: dict[str, Any],
    loan: dict[str, Any],
    risk_level: str,
    rules: CreditRules,
) -> RuleResult:
    """跑一遍内置规则（自动拒绝 / 合规 / 自动通过 / 转人工）。"""

    return evaluate_rules(applicant, loan, risk_level, rules)


def decide_route(
    risk_level: str,
    amount: float,
    rule_result: RuleResult,
    rules: CreditRules,
) -> RouteResult:
    """按审批矩阵决定审批层级与是否可自动终局。"""

    return route_approval(risk_level, amount, rule_result, rules)


def describe(rules: CreditRules) -> dict[str, Any]:
    """规则说明：中文描述 + 审批矩阵表格（供前端与文档复用）。"""

    return {
        "text": summarize_rules(rules),
        "matrix": approval_matrix_rows(rules),
        "thresholds": {
            "auto_approve_max_amount": rules.auto_approve_max_amount,
            "senior_review_min_amount": rules.senior_review_min_amount,
            "aml_amount_threshold": rules.aml_amount_threshold,
            "max_dti": rules.max_dti,
            "min_monthly_income": rules.min_monthly_income,
            "min_credit_score": rules.min_credit_score,
            "approval_timeout_minutes": rules.approval_timeout_minutes,
        },
    }
