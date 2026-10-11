"""图状态定义（信贷审批工作流）。

LangGraph 的核心思想之一是"状态是显式的"：一笔申请在审批流水线上所有需要
跨节点传递的东西都放在这个 TypedDict 里。`messages` 用 `add_messages` 归约器
——节点返回的新消息会**追加**进历史（用于 SSE 逐字推送风险评估报告），
其余键是**覆盖**语义。
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class CreditState(TypedDict, total=False):
    # 报告正文（逐字流式推送）。add_messages 负责追加与按 id 去重。
    messages: Annotated[list[AnyMessage], add_messages]
    # 申请标识与操作人（审批人 / 系统）。
    application_id: int
    operator_id: str
    # 申请人（结构化）与贷款要素。
    applicant: dict[str, Any]
    loan: dict[str, Any]
    # 提交材料： [{name, kind, text}]
    materials: list[dict[str, Any]]
    # 材料解析结果（结构化字段 + 缺失材料提示）。
    parsed_material: dict[str, Any]
    # 本轮选中的政策库（检索据此限定范围）。
    policy_libraries: list[str]
    # AI 风险评估报告 + 风险等级。
    risk_assessment: dict[str, Any]
    risk_level: str
    citations: list[dict[str, Any]]
    # 规则引擎结果 + 审批路由。
    rule_result: dict[str, Any]
    route_level: str
    route_reason: str
    # 待处理的审批人意见（HITL 中断的 resume 值）。
    pending_opinion: dict[str, Any]
    # 审批链 / 审批记录（每一级的操作、时间、意见、决策）。
    approval_chain: list[dict[str, Any]]
    approval_records: list[dict[str, Any]]
    # 最终决策。
    decision: str
    decision_reason: str
    final_conclusion: str
    # 流程类型：new（新申请）/ recheck（复审）。
    review_type: str
    # 当前环节（供 SSE 播报与 trace）。
    stage: str
    # 执行计划（复杂申请先规划）。
    plan: list[str]
    # 已执行的工具轮数，用于防止工具循环失控。
    tool_rounds: int
