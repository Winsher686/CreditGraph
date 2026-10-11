"""材料接收与解析（应用层）。

把申请人提交的材料统一成"结构化字段 + 缺失清单"，供风险评估使用：

- 结构化字段优先取申请人显式填写的值；材料文本中的关键数字（收入、负债、
  评分）可作补充/校正。
- 系统会校验必备材料（收入证明 / 征信报告 / 资产负债表），缺失项写入
  `missing_documents`，风险报告中会如实提示"材料未提供"。
- 图片材料（拍照的收入证明等）走视觉模型转写（`material_image_to_text`）。
"""

from __future__ import annotations

import base64
import re
from typing import Any

from langchain_core.messages import HumanMessage

from ..infrastructure.extract import image_mime

REQUIRED_DOCUMENTS = ("收入证明", "征信报告", "资产负债表")

_NUMBER_RE = re.compile(r"\d[\d,]*\.?\d*")


class MaterialError(ValueError):
    """材料解析失败（视觉转写失败 / 格式不支持）。"""


def _to_float(text: str) -> float | None:
    match = _NUMBER_RE.search(text or "")
    if not match:
        return None
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return None


def _probe(text: str, keywords: tuple[str, ...]) -> float | None:
    """在材料文本里找"关键词 + 数字"（如"月收入 20000 元"）。"""

    for keyword in keywords:
        index = text.find(keyword)
        if index >= 0:
            value = _to_float(text[index + len(keyword) : index + len(keyword) + 24])
            if value is not None:
                return value
    return None


def parse_materials(
    applicant: dict[str, Any], materials: list[dict[str, Any]]
) -> dict[str, Any]:
    """解析材料 → 结构化字段 + 缺失清单 + 材料索引。"""

    merged = dict(applicant or {})
    index: list[dict[str, Any]] = []
    names: list[str] = []
    for material in materials or []:
        name = str(material.get("name") or "未命名材料")
        text = str(material.get("text") or "")
        names.append(name)
        index.append({"name": name, "kind": material.get("kind", "text"), "chars": len(text)})

    joined = "\n".join(str(m.get("text") or "") for m in materials or [])

    # 材料文本补全字段（不覆盖申请人显式填写值）。
    if not merged.get("monthly_income"):
        income = _probe(joined, ("月收入", "月薪", "税后收入", "收入"))
        if income is not None:
            merged["monthly_income"] = income
    if not merged.get("monthly_debt"):
        debt = _probe(joined, ("月负债", "月还款", "每月还款", "负债"))
        if debt is not None:
            merged["monthly_debt"] = debt
    if not merged.get("credit_score"):
        score = _probe(joined, ("信用评分", "征信评分", "评分"))
        if score is not None:
            merged["credit_score"] = int(score)

    missing = [doc for doc in REQUIRED_DOCUMENTS if not any(doc in n for n in names)]
    return {
        "fields": merged,
        "materials": index,
        "missing_documents": missing,
        "provided_documents": names,
    }


async def material_image_to_text(model: Any, filename: str, data: bytes) -> str:
    """图片材料 → 结构化文本（拍照的收入证明 / 流水 / 征信报告）。

    转写失败抛 `MaterialError`——上传是显式动作，不能静默产出垃圾数据。
    """

    if model is None:
        raise MaterialError("当前没有可用的视觉模型")
    mime = image_mime(filename) or "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    prompt = (
        "你是信贷材料识别助手。把这张图片（收入证明 / 银行流水 / 征信报告 / "
        "资产负债表）转写成结构化文本，要求：\n"
        "1. 忠实于图片，逐项保留金额、日期、机构名称等关键信息；\n"
        "2. 看不清的地方标注「（此处无法辨认）」，不要编造数据；\n"
        "3. 只输出纯文本，不要额外解释。"
    )
    message = HumanMessage(
        content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ]
    )
    try:
        response = await model.ainvoke([message])
    except Exception as exc:  # noqa: BLE001 — 底层错误转成可读提示
        raise MaterialError(f"材料图片识别失败：{exc}") from exc
    text = str(getattr(response, "content", "") or "").strip()
    if text.startswith("```"):
        text = text.removeprefix("```").removeprefix("text").removeprefix("markdown")
        text = text.removesuffix("```").strip()
    if len(text) < 10:
        raise MaterialError("材料图片识别结果为空，请换一张更清晰的图片")
    return text
