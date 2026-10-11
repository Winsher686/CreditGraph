"""信贷审批的提示词模板（纯字符串，便于单测与统一维护）。

分成四块：
- `RISK_CHARTER`：风险评估的总纲（合规底线、必须给依据、不得臆造）；
- `RISK_ASSESSMENT_PROMPT`：初步风险评估报告的生成模板（要求输出 JSON）；
- `OPINION_MERGE_PROMPT`：把审批人的修改意见合并进最终结论；
- `RECHECK_PROMPT`：对已放款贷款的复审 / 抽查。
"""

from __future__ import annotations

import json
from typing import Any

RISK_CHARTER = (
    "【信贷风险评估总纲】\n"
    "1. 依据优先：判断必须引用检索到的信贷政策 / 审批规则 / 风险案例，"
    "并在报告中标注出处（政策名 + 条款摘要）；没有检索到依据时，"
    "明确说明「未命中相关政策，以下为通用口径」。\n"
    "2. 不得臆造：材料里没有的数据不要假设，缺失项标注为「材料未提供」。\n"
    "3. 合规红线：反洗钱、关联交易、行业限制、征信黑名单任一命中，"
    "必须在结论中显式提示，不得给出「直接放款」的建议。\n"
    "4. 只输出要求的 JSON，不要额外解释、不要 Markdown 代码块围栏。\n"
)

# 模型必须返回的 JSON 字段（解析与回退共用）。
RISK_REPORT_FIELDS = (
    "credit_score",
    "debt_to_income",
    "risk_level",
    "repayment_capacity",
    "summary",
    "risk_factors",
    "recommendation",
)


def build_risk_prompt(
    applicant: dict[str, Any],
    loan: dict[str, Any],
    derived: dict[str, Any],
    citations: list[dict[str, Any]],
    rule_note: str,
) -> str:
    """组装初步风险评估的用户提示词。

    关键数字都以自然语言写入，方便真实模型理解，也便于确定性 Mock 解析。
    """

    bureau = (
        "申请人命中征信黑名单。"
        if applicant.get("blacklisted")
        else "征信黑名单：否。"
    )
    policy_block = (
        "\n".join(
            f"- 【{c['library']} / {c['title']}】{c['snippet']}" for c in citations
        )
        or "（未命中相关政策，请按通用口径评估并注明）"
    )
    fields = "、".join(RISK_REPORT_FIELDS)
    return (
        f"{RISK_CHARTER}\n"
        "【申请人材料】\n"
        f"姓名：{applicant.get('name')}；年龄：{applicant.get('age')}；"
        f"职业：{applicant.get('occupation')}；行业：{applicant.get('industry')}。\n"
        f"信用评分：{applicant.get('credit_score')}；{bureau}\n"
        f"月收入：{applicant.get('monthly_income')} 元；"
        f"月负债：{applicant.get('monthly_debt')} 元。\n"
        "【贷款要素】\n"
        f"贷款金额：{loan.get('amount')} 元；期限：{loan.get('term_months')} 个月；"
        f"用途：{loan.get('purpose')}；年化利率：{loan.get('annual_rate')}。\n"
        "【派生指标（系统计算，供参考）】\n"
        f"负债率：{derived.get('debt_to_income')}；"
        f"预估月供：{derived.get('monthly_payment')} 元。\n"
        "【检索到的政策与案例依据】\n"
        f"{policy_block}\n"
        "【规则引擎预判】\n"
        f"{rule_note}\n"
        "【任务】\n"
        "请基于以上信息生成初步风险评估报告，风险等级只能是 low / medium / high 之一。"
        f"只输出一个 JSON 对象，字段为：{fields}。"
        "其中 risk_factors 为字符串数组，credit_score 为整数，"
        "debt_to_income 为 0-1 之间的小数。"
    )


def build_opinion_merge_prompt(
    assessment: dict[str, Any],
    opinion: dict[str, Any],
    level: str,
) -> str:
    """把审批人的意见 / 修改合并成最终审批结论。"""

    modified = opinion.get("modified_assessment") or {}
    return (
        "【任务】你是信贷审批意见合并助手。下面是 AI 生成的风险评估报告、"
        "以及审批人给出的意见与修改，请合并成一段最终审批结论，"
        "必须体现审批人的修改意见优先于 AI 原结论，并说明最终风险等级与放款建议。\n"
        f"【审批层级】{level}\n"
        f"【AI 原报告】{json.dumps(assessment, ensure_ascii=False)}\n"
        f"【审批人动作】{opinion.get('action')}\n"
        f"【审批人意见】{opinion.get('comment', '')}\n"
        f"【审批人修改】{json.dumps(modified, ensure_ascii=False)}\n"
        "只输出结论正文（简体中文），不要 JSON。"
    )


def build_recheck_prompt(application: dict[str, Any], citations: list[dict[str, Any]]) -> str:
    """复审 / 抽查提示词：核对放款后是否出现新的风险信号。"""

    policy_block = (
        "\n".join(f"- {c['library']} / {c['title']}" for c in citations)
        or "（未命中相关政策）"
    )
    return (
        "【任务】你是贷后复审专员。请复核这笔已放款贷款是否出现新的风险信号"
        "（还款异常、行业恶化、用途偏离、合规问题），给出「维持 / 关注 / 提前收回」"
        "三种结论之一，并说明理由。\n"
        f"【申请摘要】{json.dumps(application, ensure_ascii=False)}\n"
        f"【参考政策】\n{policy_block}\n"
        "只输出一段简体中文结论。"
    )
