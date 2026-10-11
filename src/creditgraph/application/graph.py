"""把信贷审批流程画成一张 LangGraph 状态图。

    START ─▶ intake ─▶ risk_assessment ─▶ rule_engine ─▶ approval_route
                                                            │
                        ┌───────────────────────────────────┤
                        │(自动终局)                          │(需人工)
                        ▼                                   ▼
                  final_decision                     human_approval ◀─┐
                        ▲                                   │        │
                        │                          ┌────────┴──────┐ │
                        │                          │(升级)          │(意见)
                        │                          ▼               ▼ │
                        │                      escalate      merge_opinion
                        │                                      │
                        └──────────────────────────────────────┘
                                                        final_decision ─▶ END

各节点职责（对应需求里的"节点建议"）：

- `intake`：材料接收与解析 → 结构化字段 + 缺失清单（确定性，不调用模型）。
- `risk_assessment`：检索政策依据（RAG，带出处）→ 调大模型生成风险评估报告
  （信用评分 / 还款能力 / 风险等级），解析失败回退确定性口径。
- `rule_engine`：跑内置信贷规则（自动拒绝 / 自动通过 / 转人工 / 反洗钱合规）。
- `approval_route`：按**审批矩阵**（风险等级 × 金额 × 合规）路由到审批层级。
- `human_approval`：**HITL 中断点** `interrupt()`，等待审批人确认 / 修改 / 拒绝 / 升级。
- `escalate`：升级到更高审批层级后重新中断（初级 → 高级 → 合规）。
- `merge_opinion`：把审批人的修改意见合并进最终结论（模型合并，失败回退文案）。
- `final_decision`：生成终局决策（通过 / 拒绝 / 有条件通过），写审批链。

图本身**不写数据库**——数据库写入由接口层在一次工作流跑完后统一落库，
避免与 AsyncSqliteSaver 检查点抢同一文件的写锁。
"""

from __future__ import annotations

import asyncio
import datetime
import json
import re
import time
from collections.abc import Callable
from typing import Any

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..domain.credit import (
    DECISION_APPROVED,
    DECISION_CONDITIONAL,
    DECISION_PENDING,
    DECISION_REJECTED,
    LEVEL_AUTO,
    LEVEL_COMPLIANCE,
    LEVEL_JUNIOR,
    LEVEL_LABELS,
    LEVEL_SENIOR,
    RISK_LABELS,
    CreditRules,
    RouteResult,
    RuleResult,
    next_approval_level,
)
from ..domain.credit_prompts import (
    build_opinion_merge_prompt,
    build_risk_prompt,
)
from ..domain.model_routing import model_tier
from .credit_rules import decide_route, describe, evaluate
from .material_analysis import parse_materials
from .ports import ChatModelPort, UsageRepositoryPort
from .routing import (
    ACTION_APPROVE,
    ACTION_ESCALATE,
    ACTION_MODIFY,
    ACTION_REJECT,
    APPROVAL_ACTIONS,
    RISK_ANALYST,
    allowed_tools,
    profile_for_stage,
)
from .state import CreditState
from .tools import (
    get_tool,
    hits_to_citations,
    repayment_summary,
    search_policies,
    set_current_libraries,
    set_current_operator,
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _today_start() -> float:
    now = datetime.datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _model_label(model: Any) -> str:
    return str(
        getattr(model, "model_name", None) or getattr(model, "_llm_type", "unknown")
    )


async def _record_usage(repository, operator_id: str, model: Any, message: Any) -> None:
    """把这次模型调用的 token 用量记进账单（没有用量信息就跳过）。

    写库放在线程里执行：同步的 sqlite 写会阻塞事件循环，导致同一文件上的
    检查点连接无法释放锁（表现为 `database is locked`）。
    """

    if repository is None:
        return
    usage = getattr(message, "usage_metadata", None)
    if not usage:
        return
    await asyncio.to_thread(
        repository.record,
        user_id=operator_id,
        model=_model_label(model),
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        total_tokens=int(usage.get("total_tokens", 0)),
        created_at=time.time(),
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出里抠出第一个 JSON 对象（真实模型常带前后说明）。"""

    match = _JSON_RE.search(text or "")
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _normalize_assessment(
    raw: dict[str, Any] | None,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    """把模型输出规整成报告结构；字段缺失或非法时用确定性兜底值补齐。"""

    data = dict(fallback)
    if raw:
        for key in fallback:
            if key in raw and raw[key] not in (None, "", []):
                data[key] = raw[key]
    risk = str(data.get("risk_level") or "medium").lower()
    if risk not in ("low", "medium", "high"):
        risk = "medium"
    data["risk_level"] = risk
    try:
        data["credit_score"] = int(data.get("credit_score") or 0)
    except (TypeError, ValueError):
        data["credit_score"] = 0
    try:
        data["debt_to_income"] = round(float(data.get("debt_to_income") or 0.0), 4)
    except (TypeError, ValueError):
        data["debt_to_income"] = 0.0
    factors = data.get("risk_factors")
    data["risk_factors"] = [str(f) for f in factors] if isinstance(factors, list) else []
    return data


def render_risk_report(assessment: dict[str, Any], citations: list[dict[str, Any]]) -> str:
    """把风险评估报告渲染成审批人可读的 Markdown（SSE 逐字推送的正文）。"""

    risk = str(assessment.get("risk_level") or "medium")
    lines = [
        f"## 初步风险评估报告（{RISK_LABELS.get(risk, risk)}）",
        "",
        f"- 信用评分：**{assessment.get('credit_score', 0)}**",
        f"- 负债率：**{float(assessment.get('debt_to_income') or 0):.0%}**",
        f"- 还款能力：{assessment.get('repayment_capacity', '')}",
        f"- 风险等级：**{RISK_LABELS.get(risk, risk)}**",
        "",
        f"**结论**：{assessment.get('summary', '')}",
        f"**建议**：{assessment.get('recommendation', '')}",
    ]
    factors = assessment.get("risk_factors") or []
    if factors:
        lines += ["", "**风险点**："] + [f"- {item}" for item in factors]
    if citations:
        lines += ["", "**政策依据**："] + [
            f"- 【{c['library']} / {c['title']}】{c['snippet']}" for c in citations
        ]
    else:
        lines += ["", "**政策依据**：未命中相关政策，以上为通用口径。"]
    return "\n".join(lines)


def _render_rule_result(rule_result: RuleResult) -> str:
    parts: list[str] = []
    if rule_result.auto_reject:
        parts.append("触发自动拒绝：" + "；".join(rule_result.reject_reasons))
    if rule_result.auto_approve:
        parts.append("触发自动通过：" + "；".join(rule_result.approve_reasons))
    if rule_result.requires_compliance:
        parts.append("触发合规审查：" + "；".join(rule_result.compliance_reasons))
    if not parts:
        parts.append("未触发自动规则，进入人工审批。")
    return "【规则引擎】" + " ".join(parts)


def build_graph(
    *,
    model: ChatModelPort,
    checkpointer: Any,
    rules: CreditRules | None = None,
    default_policy_libraries: list[str] | None = None,
    default_annual_rate: float = 0.049,
    max_tool_rounds: int = 4,
    tool_timeout: float = 10.0,
    tool_retries: int = 2,
    max_history_messages: int = 24,
    model_router: Callable[[str], ChatModelPort] | None = None,
    usage_repository: UsageRepositoryPort | None = None,
    daily_token_budget: int = 0,
    budget_exceeded_action: str = "block",
):
    """编译信贷审批状态图。`checkpointer` 负责持久化与断点恢复（含 HITL 中断）。"""

    active_rules = rules or CreditRules()
    policy_libraries = default_policy_libraries or []

    # ------------------------------------------------------------------ intake
    def intake_node(state: CreditState) -> dict[str, Any]:
        parsed = parse_materials(
            state.get("applicant") or {}, state.get("materials") or []
        )
        missing = parsed["missing_documents"]
        provided = parsed["provided_documents"]
        text = "【材料接收】已收到材料：" + ("、".join(provided) or "无") + "。"
        if missing:
            text += " 缺失必备材料：" + "、".join(missing) + "（风险报告将标注为材料未提供）。"
        else:
            text += " 必备材料齐全。"
        return {
            "applicant": parsed["fields"],
            "parsed_material": parsed,
            "stage": "intake",
            "messages": [AIMessage(content=text)],
        }

    # -------------------------------------------------------- risk_assessment
    async def risk_assessment_node(state: CreditState) -> dict[str, Any]:
        applicant = state.get("applicant") or {}
        loan = state.get("loan") or {}
        income = float(applicant.get("monthly_income") or 0.0)
        debt = float(applicant.get("monthly_debt") or 0.0)
        amount = float(loan.get("amount") or 0.0)
        term = int(loan.get("term_months") or 12)
        rate = float(loan.get("annual_rate") or default_annual_rate)

        derived = repayment_summary(income, debt, amount, term, rate)
        rule_note = describe(active_rules)["text"]

        # 检索政策依据（RAG）：用途 + 行业 + 金额档 组成查询。
        query = " ".join(
            part
            for part in (
                str(loan.get("purpose") or ""),
                str(applicant.get("industry") or ""),
                "额度" if amount >= active_rules.senior_review_min_amount else "",
                "风险" if state.get("risk_level") == "high" else "",
            )
            if part
        ).strip()
        libraries = state.get("policy_libraries") or policy_libraries
        set_current_libraries(libraries)
        set_current_operator(str(state.get("operator_id") or "system"))
        hits = search_policies(query, libraries=libraries, limit=4)
        citations = hits_to_citations(hits)

        prompt = build_risk_prompt(
            applicant, loan, derived, citations, rule_note=rule_note
        )
        profile = profile_for_stage(RISK_ANALYST)
        tools = [tool for name in allowed_tools(profile) if (tool := get_tool(name))]

        tier = model_tier("risk_assessment", prompt)
        active_model = model_router(tier) if model_router else model

        operator_id = str(state.get("operator_id") or "local")
        if (
            usage_repository is not None
            and daily_token_budget > 0
            and budget_exceeded_action == "block"
        ):
            used = await asyncio.to_thread(
                usage_repository.total_since, user_id=operator_id, since=_today_start()
            )
            if used >= daily_token_budget:
                return {
                    "stage": "risk_assessment",
                    "messages": [AIMessage(content="今日 token 预算已用完，暂停风险分析。")],
                }

        bound = active_model.bind_tools(tools) if tools else active_model
        messages: list[Any] = [SystemMessage(profile.system_prompt), HumanMessage(prompt)]

        aggregated: AIMessageChunk | None = None
        async for chunk in bound.astream(messages):
            aggregated = chunk if aggregated is None else aggregated + chunk
        if aggregated is None:
            aggregated = AIMessageChunk(content="")
        await _record_usage(usage_repository, operator_id, active_model, aggregated)

        fallback = _fallback_assessment(applicant, loan, derived)
        parsed = _normalize_assessment(_extract_json(str(aggregated.content)), fallback)
        parsed["citations"] = citations

        report = render_risk_report(parsed, citations)
        return {
            "risk_assessment": parsed,
            "risk_level": parsed["risk_level"],
            "citations": citations,
            "stage": "risk_assessment",
            "messages": [AIMessage(content=report)],
        }

    def _fallback_assessment(
        applicant: dict[str, Any], loan: dict[str, Any], derived: dict[str, float]
    ) -> dict[str, Any]:
        from ..domain.credit import heuristic_risk

        base = heuristic_risk(applicant, loan, active_rules)
        base.setdefault("monthly_payment", derived.get("monthly_payment", 0.0))
        return base

    # ------------------------------------------------------------- rule_engine
    def rule_engine_node(state: CreditState) -> dict[str, Any]:
        result = evaluate(
            state.get("applicant") or {},
            state.get("loan") or {},
            str(state.get("risk_level") or "medium"),
            active_rules,
        )
        return {
            "rule_result": result.to_dict(),
            "stage": "rule_engine",
            "messages": [AIMessage(content=_render_rule_result(result))],
        }

    # ---------------------------------------------------------- approval_route
    def approval_route_node(state: CreditState) -> dict[str, Any]:
        if state.get("review_type") == "recheck":
            return {
                "route_level": LEVEL_SENIOR,
                "route_reason": "贷后复审，由高级审批人核定维持 / 关注 / 提前收回",
                "decision": DECISION_PENDING,
                "stage": "approval_route",
                "messages": [AIMessage(content="【审批路由】贷后复审 → 高级审批人。")],
            }
        rule_result = RuleResult.from_dict(state.get("rule_result"))
        amount = float((state.get("loan") or {}).get("amount") or 0.0)
        route: RouteResult = decide_route(
            str(state.get("risk_level") or "medium"), amount, rule_result, active_rules
        )
        text = (
            f"【审批路由】{route.reason} → {LEVEL_LABELS.get(route.level, route.level)}。"
        )
        return {
            "route_level": route.level,
            "route_reason": route.reason,
            "decision": route.decision
            if route.level == LEVEL_AUTO
            else DECISION_PENDING,
            "stage": "approval_route",
            "messages": [AIMessage(content=text)],
        }

    def after_route(state: CreditState) -> str:
        if state.get("route_level") == LEVEL_AUTO:
            return "final_decision"
        return "human_approval"

    # ---------------------------------------------------------- human_approval
    def human_approval_node(state: CreditState) -> dict[str, Any]:
        level = str(state.get("route_level") or LEVEL_JUNIOR)
        payload = {
            "action": "approval_required",
            "application_id": state.get("application_id"),
            "review_type": state.get("review_type") or "new",
            "level": level,
            "level_label": LEVEL_LABELS.get(level, level),
            "risk_level": state.get("risk_level"),
            "risk_assessment": state.get("risk_assessment"),
            "rule_result": state.get("rule_result"),
            "route_reason": state.get("route_reason"),
            "applicant": state.get("applicant"),
            "loan": state.get("loan"),
            "citations": state.get("citations"),
            "options": list(APPROVAL_ACTIONS),
        }
        opinion = interrupt(payload)
        if isinstance(opinion, str):
            opinion = {"action": opinion}
        opinion = dict(opinion or {})
        opinion.setdefault("action", ACTION_APPROVE)
        return {"pending_opinion": opinion, "stage": "human_approval"}

    def after_human(state: CreditState) -> str:
        action = (state.get("pending_opinion") or {}).get("action")
        if action == ACTION_ESCALATE:
            return "escalate"
        return "merge_opinion"

    # ---------------------------------------------------------------- escalate
    def escalate_node(state: CreditState) -> dict[str, Any]:
        current = str(state.get("route_level") or LEVEL_JUNIOR)
        nxt = next_approval_level(current) or LEVEL_COMPLIANCE
        now = time.time()
        opinion = state.get("pending_opinion") or {}
        actor = str(opinion.get("approver") or state.get("operator_id") or "system")
        entry = {
            "actor": actor,
            "action": ACTION_ESCALATE,
            "from_level": current,
            "to_level": nxt,
            "detail": str(opinion.get("comment") or "初级审批人无法决定，升级处理"),
            "created_at": now,
        }
        return {
            "route_level": nxt,
            "pending_opinion": {},
            "approval_chain": [*(state.get("approval_chain") or []), entry],
            "stage": "escalate",
            "messages": [
                AIMessage(
                    content=(
                        f"【升级】{LEVEL_LABELS.get(current, current)} → "
                        f"{LEVEL_LABELS.get(nxt, nxt)}，重新等待审批。"
                    )
                )
            ],
        }

    # ----------------------------------------------------------- merge_opinion
    async def merge_opinion_node(state: CreditState) -> dict[str, Any]:
        opinion = state.get("pending_opinion") or {}
        level = str(state.get("route_level") or LEVEL_JUNIOR)
        assessment = dict(state.get("risk_assessment") or {})
        modified = opinion.get("modified_assessment") or {}
        if isinstance(modified, dict) and modified:
            assessment.update(modified)

        now = time.time()
        approver = str(opinion.get("approver") or state.get("operator_id") or "unknown")
        record = {
            "approver": approver,
            "level": level,
            "decision": str(opinion.get("action") or ""),
            "comment": str(opinion.get("comment") or ""),
            "modified": bool(modified),
            "created_at": now,
        }
        chain_entry = {
            "actor": approver,
            "action": str(opinion.get("action") or ""),
            "from_level": level,
            "to_level": level,
            "detail": str(opinion.get("comment") or "")[:200],
            "created_at": now,
        }

        conclusion = await _merge_conclusion(state, assessment, opinion, level)

        return {
            "risk_assessment": assessment,
            "final_conclusion": conclusion,
            "approval_records": [*(state.get("approval_records") or []), record],
            "approval_chain": [*(state.get("approval_chain") or []), chain_entry],
            "stage": "opinion_merge",
            "messages": [AIMessage(content=conclusion)],
        }

    async def _merge_conclusion(
        state: CreditState,
        assessment: dict[str, Any],
        opinion: dict[str, Any],
        level: str,
    ) -> str:
        prompt = build_opinion_merge_prompt(assessment, opinion, level)
        profile = profile_for_stage("opinion_merger")
        try:
            response = await model.ainvoke(
                [SystemMessage(profile.system_prompt), HumanMessage(prompt)]
            )
            text = str(getattr(response, "content", "") or "").strip()
            if text:
                return text
        except Exception:  # noqa: BLE001 — 合并失败不炸流程，退回确定性文案
            pass
        return _deterministic_conclusion(state, assessment, opinion, level)

    def _deterministic_conclusion(
        state: CreditState,
        assessment: dict[str, Any],
        opinion: dict[str, Any],
        level: str,
    ) -> str:
        action = str(opinion.get("action") or ACTION_APPROVE)
        risk = str(assessment.get("risk_level") or state.get("risk_level") or "medium")
        verdict = {
            ACTION_APPROVE: "同意放款",
            ACTION_MODIFY: "修改后放款",
            ACTION_REJECT: "拒绝本次申请",
        }.get(action, "维持原结论")
        comment = str(opinion.get("comment") or "").strip()
        return (
            f"经{LEVEL_LABELS.get(level, level)}审核，采纳审批人意见（{action}），"
            f"最终风险等级为{RISK_LABELS.get(risk, risk)}，结论：{verdict}。"
            + (f" 审批意见：{comment}" if comment else "")
        )

    # ---------------------------------------------------------- final_decision
    def final_decision_node(state: CreditState) -> dict[str, Any]:
        now = time.time()
        rule_level = str(state.get("route_level") or "")
        opinion = state.get("pending_opinion") or {}
        records = state.get("approval_records") or []
        conclusion = str(state.get("final_conclusion") or "")
        chain = list(state.get("approval_chain") or [])

        if rule_level == LEVEL_AUTO or not records:
            decision = str(state.get("decision") or DECISION_PENDING)
            reason = str(state.get("route_reason") or "")
            if not conclusion:
                conclusion = (
                    f"规则引擎自动裁决：{reason}，最终决策 {decision}。"
                )
            chain.append(
                {
                    "actor": "system",
                    "action": decision,
                    "from_level": LEVEL_AUTO,
                    "to_level": decision,
                    "detail": reason,
                    "created_at": now,
                }
            )
        else:
            action = str(opinion.get("action") or ACTION_APPROVE)
            decision = {
                ACTION_REJECT: DECISION_REJECTED,
                ACTION_MODIFY: DECISION_CONDITIONAL,
            }.get(action, DECISION_APPROVED)
            reason = str(opinion.get("comment") or state.get("route_reason") or "")
            if not conclusion:
                conclusion = reason or "审批完成。"
            chain.append(
                {
                    "actor": str(opinion.get("approver") or state.get("operator_id") or "system"),
                    "action": decision,
                    "from_level": rule_level,
                    "to_level": decision,
                    "detail": reason,
                    "created_at": now,
                }
            )

        if state.get("review_type") == "recheck":
            conclusion = "【贷后复审】" + conclusion

        # 人工路径的结论已在 merge_opinion 节点推送过，这里不再重复推送。
        messages = [] if state.get("final_conclusion") else [AIMessage(content=conclusion)]
        return {
            "decision": decision,
            "decision_reason": reason,
            "final_conclusion": conclusion,
            "approval_chain": chain,
            "stage": "final_decision",
            "messages": messages,
        }

    builder = StateGraph(CreditState)
    builder.add_node("intake", intake_node)
    builder.add_node("risk_assessment", risk_assessment_node)
    builder.add_node("rule_engine", rule_engine_node)
    builder.add_node("approval_route", approval_route_node)
    builder.add_node("human_approval", human_approval_node)
    builder.add_node("escalate", escalate_node)
    builder.add_node("merge_opinion", merge_opinion_node)
    builder.add_node("final_decision", final_decision_node)

    builder.add_edge(START, "intake")
    builder.add_edge("intake", "risk_assessment")
    builder.add_edge("risk_assessment", "rule_engine")
    builder.add_edge("rule_engine", "approval_route")
    builder.add_conditional_edges(
        "approval_route",
        after_route,
        {"final_decision": "final_decision", "human_approval": "human_approval"},
    )
    builder.add_conditional_edges(
        "human_approval",
        after_human,
        {"escalate": "escalate", "merge_opinion": "merge_opinion"},
    )
    builder.add_edge("escalate", "human_approval")
    builder.add_edge("merge_opinion", "final_decision")
    builder.add_edge("final_decision", END)
    return builder.compile(checkpointer=checkpointer)


__all__ = ["build_graph", "render_risk_report"]
