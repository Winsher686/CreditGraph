"""模型路由（Model Routing）：按审批环节的复杂度选择模型档位（纯函数）。

用"小模型跑简单环节、大模型接复杂环节"来省成本、控延迟：
- 材料解析 / 复审摘要这类机械环节 → small；
- 初步风险评估这类核心判断（或超长材料）→ large；
- 其余（意见合并等）→ standard。
"""

from __future__ import annotations

_STAGES = ("intake", "risk_assessment", "approval_route", "recheck", "opinion_merge")

_SMALL_STAGES = {"intake", "recheck"}
_LARGE_STAGES = {"risk_assessment"}
_LARGE_LENGTH = 800


def model_tier(stage: str, text: str = "") -> str:
    """返回 ``"small"`` / ``"standard"`` / ``"large"``。"""

    if stage in _SMALL_STAGES:
        return "small"
    if stage in _LARGE_STAGES or len((text or "").strip()) > _LARGE_LENGTH:
        return "large"
    return "standard"
