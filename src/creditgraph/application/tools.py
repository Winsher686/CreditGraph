"""信贷审批可调用的工具。

内置工具（供图节点与模型调用，逻辑集中在**纯函数**里便于复用与测试）：

- `policy_search`：在信贷政策 / 审批规则 / 风险案例库里检索，返回带出处的片段。
- `risk_calc`：按等额本息计算月供、负债率与建议额度（确定性计算，不心算）。
- `calculator`：安全算术求值，杜绝直接 eval 注入。

工具依赖的是**端口**（`KnowledgePort`），不是具体实现。两样"本轮上下文"
用 contextvar 传递，避免污染模型可见的参数 schema：本轮选中的政策库、
当前操作人。注意 LangGraph 各节点在独立 context 里执行，需要时各自 set 一次。
"""

from __future__ import annotations

import ast
import contextvars
import operator
from typing import Any

from langchain_core.tools import tool

from ..domain.credit import CreditRules, debt_to_income, monthly_payment
from ..domain.models import SearchHit
from .ports import KnowledgePort

_knowledge: KnowledgePort | None = None
_current_libraries: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "creditgraph_current_libraries", default=None
)
_current_operator: contextvars.ContextVar[str] = contextvars.ContextVar(
    "creditgraph_current_operator", default="system"
)

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}


def configure(knowledge: KnowledgePort | None) -> None:
    """由组合根注入知识库实现。"""

    global _knowledge
    _knowledge = knowledge


def get_knowledge() -> KnowledgePort:
    if _knowledge is None:
        raise RuntimeError("工具尚未配置知识库：请先调用 tools.configure(...)")
    return _knowledge


def set_current_libraries(libraries: list[str] | None) -> None:
    _current_libraries.set(list(libraries) if libraries else None)


def set_current_operator(operator_id: str) -> None:
    _current_operator.set(operator_id or "system")


# ---------------------------------------------------------------------------
# 政策检索（带出处）
# ---------------------------------------------------------------------------


def search_policies(
    query: str, *, libraries: list[str] | None = None, limit: int = 4
) -> list[SearchHit]:
    """在信贷政策知识库做混合检索；查询为空时退回最近政策概览。"""

    cleaned = (query or "").strip()
    store = get_knowledge()
    selected = libraries if libraries is not None else _current_libraries.get()
    if cleaned:
        return store.search(cleaned, libraries=selected, limit=limit)
    # 查询为空：用库里前若干片段兜底，保证报告仍有依据可引。
    chunks = store.list_chunks(library=(selected[0] if selected else None))
    return [
        SearchHit(
            chunk_id=chunk.id,
            library=chunk.library,
            title=chunk.title,
            content=chunk.content,
            score=0.0,
        )
        for chunk in chunks[:limit]
    ]


def hits_to_citations(hits: list[SearchHit]) -> list[dict[str, Any]]:
    """把检索命中转成"出处"结构（前端与报告共用）。"""

    citations: list[dict[str, Any]] = []
    for hit in hits:
        citations.append(
            {
                "library": hit.library,
                "title": hit.title,
                "chunk_id": hit.chunk_id,
                "snippet": hit.content[:160].replace("\n", " "),
                "score": round(float(hit.score), 4),
            }
        )
    return citations


def format_citations(citations: list[dict[str, Any]]) -> str:
    if not citations:
        return "未命中相关政策。"
    return "\n".join(
        f"【{c['library']} / {c['title']}】{c['snippet']}" for c in citations
    )


# ---------------------------------------------------------------------------
# 计算工具
# ---------------------------------------------------------------------------


def _format_number(value: float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp):
        operation = _BIN_OPS.get(type(node.op))
        if operation is None:
            raise ValueError("不支持的运算符")
        return operation(_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.USub):
            return -_eval_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +_eval_node(node.operand)
    raise ValueError("表达式只允许数字、括号和四则运算")


def safe_eval(expression: str) -> float:
    """只做算术的安全求值：解析成语法树后按白名单运算，不做真实 eval。"""

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("算式语法有误") from exc
    return _eval_node(tree.body)


def repayment_summary(
    monthly_income: float,
    monthly_debt: float,
    loan_amount: float,
    term_months: int,
    annual_rate: float = 0.0,
) -> dict[str, float]:
    """确定性还款能力指标：月供、负债率、月供收入比、建议额度。"""

    dti = debt_to_income(monthly_income, monthly_debt)
    payment = monthly_payment(loan_amount, term_months, annual_rate)
    ratio = round(payment / monthly_income, 4) if monthly_income > 0 else 9.99
    # 建议额度：在"月供不超过月收入 50%"的前提下反推可承受本金（近似按当前期限/利率）。
    affordable_payment = monthly_income * 0.5 - monthly_debt
    suggested = 0.0
    if affordable_payment > 0 and payment > 0:
        suggested = round(loan_amount * affordable_payment / payment, 2)
    return {
        "debt_to_income": dti,
        "monthly_payment": payment,
        "payment_to_income": ratio,
        "suggested_amount": max(0.0, suggested),
    }


@tool
def calculator(expression: str) -> str:
    """计算一个数学表达式，例如 "7 * 9" 或 "(2 + 3) / 4"。只做算术，不解释。"""

    try:
        return _format_number(safe_eval(expression))
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        return f"计算出错：{exc}"


@tool
def policy_search(query: str) -> str:
    """在信贷政策 / 审批规则 / 风险案例知识库里检索，返回相关片段和出处。
    生成风险评估报告、核对合规要求时使用。"""

    hits = search_policies(query)
    if not hits:
        return "知识库里没有找到相关政策或案例。"
    return format_citations(hits_to_citations(hits))


@tool
def risk_calc(
    monthly_income: float,
    monthly_debt: float,
    loan_amount: float,
    term_months: int,
    annual_rate: float = 0.0,
) -> str:
    """计算还款能力指标：负债率、等额本息月供、月供收入比与建议额度。
    需要量化"还款能力"时使用，不要心算。"""

    result = repayment_summary(
        monthly_income, monthly_debt, loan_amount, term_months, annual_rate
    )
    return (
        f"负债率 {result['debt_to_income']:.0%}；"
        f"月供 {result['monthly_payment']:.2f} 元；"
        f"月供收入比 {result['payment_to_income']:.0%}；"
        f"建议额度 {result['suggested_amount']:.0f} 元"
    )


TOOLS = {
    "calculator": calculator,
    "policy_search": policy_search,
    "risk_calc": risk_calc,
}


def get_tool(name: str):
    return TOOLS.get(name)


def default_rules() -> CreditRules:
    return CreditRules()
