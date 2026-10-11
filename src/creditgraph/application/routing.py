"""审批角色与工具权限（纯规则实现）。

一笔申请在流水线上会被不同的"角色"处理——风险评估分析师、政策检索员、
意见合并员、复审专员。每个角色有自己的系统提示词和**工具最小权限**
（默认工具 + 黑名单），由 `application/tools.py` 里的工具集提供能力。

规则是确定性的、零成本、可单测；LLM 只是"执行者"，权限边界在这里写死。
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 审批动作（HITL 中断后审批人可做的操作）
# ---------------------------------------------------------------------------

ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"
ACTION_MODIFY = "modify"
ACTION_ESCALATE = "escalate"
APPROVAL_ACTIONS = (ACTION_APPROVE, ACTION_REJECT, ACTION_MODIFY, ACTION_ESCALATE)
ACTION_LABELS = {
    ACTION_APPROVE: "审批通过",
    ACTION_REJECT: "审批拒绝",
    ACTION_MODIFY: "修改风险评估后提交",
    ACTION_ESCALATE: "升级到更高级审批人",
}

# 角色标识
RISK_ANALYST = "risk_analyst"
POLICY_RETRIEVER = "policy_retriever"
OPINION_MERGER = "opinion_merger"
RECHECKER = "rechecker"
INTAKE = "intake"


@dataclass(frozen=True)
class StageProfile:
    stage: str
    system_prompt: str
    default_tools: tuple[str, ...] = ()
    denied_tools: tuple[str, ...] = ()
    deny_all: bool = False


AGENT_PROFILES: dict[str, StageProfile] = {
    INTAKE: StageProfile(
        stage=INTAKE,
        system_prompt=(
            "你是材料接收与解析助手。把申请人提交的材料（收入证明、征信报告、"
            "资产负债表等）整理成结构化字段，材料缺失或看不清的地方如实标注，"
            "不要臆造数据。本环节只做整理，不下风险结论。"
        ),
        deny_all=True,  # 解析是确定性流程，不需要任何工具
    ),
    RISK_ANALYST: StageProfile(
        stage=RISK_ANALYST,
        system_prompt=(
            "你是信贷风险评估分析师。基于申请人材料与检索到的政策依据，"
            "生成初步风险评估报告：信用评分、还款能力分析、风险等级"
            "（low / medium / high）。所有判断都要能追溯到引用到的政策条款；"
            "没有依据时明确说明「未命中相关政策，以下为通用口径」。"
            "不得给出绕过合规红线的结论。"
        ),
        default_tools=("policy_search", "risk_calc", "calculator"),
    ),
    POLICY_RETRIEVER: StageProfile(
        stage=POLICY_RETRIEVER,
        system_prompt=(
            "你是信贷政策检索助手。调用检索工具找出与申请相关的信贷政策、"
            "审批规则与风险案例，逐条给出出处（哪个库、哪份文件）。"
            "检索不到就明确说明，不要用通用知识冒充政策依据。"
        ),
        default_tools=("policy_search",),
        denied_tools=("calculator",),
    ),
    OPINION_MERGER: StageProfile(
        stage=OPINION_MERGER,
        system_prompt=(
            "你是审批意见合并助手。把审批人的修改意见合并进 AI 生成的风险评估"
            "结论，审批人的判断优先于 AI 原结论，输出一段清晰的最终审批结论。"
        ),
        deny_all=True,
    ),
    RECHECKER: StageProfile(
        stage=RECHECKER,
        system_prompt=(
            "你是贷后复审专员。复核已放款贷款是否出现新的风险信号"
            "（还款异常、行业恶化、用途偏离、合规问题），给出「维持 / 关注 / "
            "提前收回」结论并说明理由。可以检索政策与风险案例作为依据。"
        ),
        default_tools=("policy_search",),
    ),
}


def profile_for_stage(stage: str) -> StageProfile:
    return AGENT_PROFILES.get(stage, AGENT_PROFILES[RISK_ANALYST])


def allowed_tools(
    profile: StageProfile, requested: list[str] | None = None
) -> list[str]:
    """算出这个角色最终可用的工具名。

    规则：`deny_all` 一律清空；否则取"显式请求的工具（若有）否则默认集"，
    再统一减去黑名单。**黑名单优先于显式请求**，否则绕过一下就能拿回被禁能力。
    """

    if profile.deny_all:
        return []
    base = list(requested) if requested else list(profile.default_tools)
    denied = set(profile.denied_tools)
    seen: list[str] = []
    for name in base:
        if name not in denied and name not in seen:
            seen.append(name)
    return seen


def is_tool_allowed(profile: StageProfile, name: str) -> bool:
    """执行层权限判定：即使模型硬发一个被禁工具，也在这里被拦下。"""

    if profile.deny_all:
        return False
    return name not in profile.denied_tools
