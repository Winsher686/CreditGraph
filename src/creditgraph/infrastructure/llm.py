"""模型适配（基础设施）：Mock（默认，零成本）与 OpenAI 兼容（可选）。

Mock 模型按固定规则**模拟信贷审批场景的输出**：
- 看到"初步风险评估报告"任务 → 从提示词里解析申请人数字，产出结构化 JSON 报告；
- 看到"合并审批意见"任务 → 产出确定性的合并结论；
- 看到"贷后复审"任务 → 产出复审结论。

这样全链路（材料解析 → 风险评估 → 规则引擎 → 审批路由 → HITL → 意见合并 →
最终决策 → 复审）都能在**零 API 消耗**下被确定性地测试和演示。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from ..config import Settings
from ..domain.credit import CreditRules, debt_to_income, monthly_payment

_RISK_TASK_MARKER = "只输出一个 JSON 对象"
_MERGE_TASK_MARKER = "合并成一段最终审批结论"
_RECHECK_TASK_MARKER = "贷后复审专员"

_NUM = r"([\d,]+(?:\.\d+)?)"


def _num(text: str, label: str) -> float | None:
    match = re.search(label + r"[:：]\s*" + _NUM, text or "")
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, dict):
        return (tool.get("function") or {}).get("name")
    return getattr(tool, "name", None) or getattr(tool, "__name__", None)


def _all_text(messages: Sequence[BaseMessage]) -> str:
    return "\n".join(str(getattr(m, "content", "")) for m in messages)


def _last_human_text(messages: Sequence[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


class MockChatModel(BaseChatModel):
    """确定性的假模型：按提示词里的任务标记产出固定结构的回答。"""

    tool_names: list[str] = Field(default_factory=list)
    rules: CreditRules = Field(default_factory=CreditRules)

    @property
    def _llm_type(self) -> str:
        return "credit-mock"

    def bind_tools(
        self, tools: Sequence[Any], *, tool_choice: str | None = None, **kwargs: Any
    ) -> MockChatModel:
        names = [name for tool in tools if (name := _tool_name(tool))]
        return self.model_copy(update={"tool_names": names})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = self._decide(messages)
        message = message.model_copy(
            update={"usage_metadata": self._estimate_usage(messages, message)}
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    @staticmethod
    def _estimate_usage(messages: list[BaseMessage], message: AIMessage) -> dict[str, int]:
        """粗略估 token（字符数/2）——让 Mock 也能驱动 token 记账与预算。"""

        prompt_chars = sum(len(str(getattr(m, "content", ""))) for m in messages)
        output_chars = len(str(getattr(message, "content", "")))
        input_tokens = prompt_chars // 2 + 1
        output_tokens = output_chars // 2 + 1
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }

    def _decide(self, messages: list[BaseMessage]) -> AIMessage:
        text = _all_text(messages)
        if _RISK_TASK_MARKER in text and "初步风险评估报告" in text:
            return AIMessage(content=json.dumps(self._risk_report(text), ensure_ascii=False))
        if _MERGE_TASK_MARKER in text:
            return AIMessage(
                content=(
                    "（模拟模型）已合并审批意见：审批人的判断优先于 AI 原结论，"
                    "最终风险等级与放款建议已按审批人修改口径更新。"
                )
            )
        if _RECHECK_TASK_MARKER in text:
            return AIMessage(
                content="（模拟模型）贷后复审结论：维持。未发现新的风险信号。"
            )
        topic = _last_human_text(messages).strip()[:60] or "本次请求"
        return AIMessage(content=f"（模拟模型）已处理：{topic}")

    def _risk_report(self, text: str) -> dict[str, Any]:
        """从提示词里解析申请人数据，产出确定性的风险评估 JSON。"""

        rules = self.rules
        income = _num(text, "月收入") or 0.0
        debt = _num(text, "月负债") or 0.0
        score = int(_num(text, "信用评分") or 0)
        amount = _num(text, "贷款金额") or 0.0
        term = int(_num(text, "期限") or 12)
        rate = _num(text, "年化利率") or 0.0
        blacklisted = "命中征信黑名单" in text

        dti = debt_to_income(income, debt)
        payment = monthly_payment(amount, term, rate)

        if blacklisted or score < rules.high_risk_score_below or dti > rules.max_dti:
            level = "high"
        elif score >= rules.low_risk_score_at_least and dti <= rules.auto_approve_max_dti:
            level = "low"
        else:
            level = "medium"

        factors: list[str] = []
        if blacklisted:
            factors.append("申请人命中征信黑名单")
        if income < rules.min_monthly_income:
            factors.append("月收入低于最低要求")
        if dti > rules.max_dti:
            factors.append(f"负债率 {dti:.0%} 超过上限")

        capacity = (
            f"月供约 {payment:.0f} 元，月供收入比 "
            f"{(payment / income if income else 1):.0%}，负债率 {dti:.0%}"
        )
        risk_label = {"low": "低风险", "medium": "中风险", "high": "高风险"}[level]
        return {
            "credit_score": score,
            "debt_to_income": dti,
            "risk_level": level,
            "repayment_capacity": capacity,
            "summary": f"综合信用评分 {score}、负债率 {dti:.0%}，判定为{risk_label}。",
            "risk_factors": factors,
            "recommendation": {
                "low": "建议按标准流程放款",
                "medium": "建议转人工审批，核实收入与负债",
                "high": "建议拒绝或要求补充担保 / 降低额度",
            }[level],
        }


def build_chat_model(settings: Settings) -> BaseChatModel:
    provider = settings.llm_provider
    if provider in ("", "mock"):
        return MockChatModel()
    if provider in ("openai", "openai-compatible"):
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "接入真实模型需要 langchain-openai：请执行 `uv sync --extra openai`"
            ) from exc
        return ChatOpenAI(
            model=settings.model,
            base_url=settings.openai_base_url or None,
            api_key=settings.openai_api_key or "not-needed",
            max_tokens=settings.max_output_tokens,
            temperature=0.2,
        )
    raise ValueError(f"未知的 LLM Provider：{provider}")
