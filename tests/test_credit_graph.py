from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from creditgraph.application import tools
from creditgraph.application.graph import build_graph
from creditgraph.domain.credit import (
    DECISION_APPROVED,
    DECISION_AUTO_APPROVED,
    DECISION_CONDITIONAL,
    DECISION_REJECTED,
    LEVEL_JUNIOR,
    LEVEL_SENIOR,
)
from creditgraph.infrastructure.knowledge import KnowledgeStore
from creditgraph.infrastructure.llm import MockChatModel

POLICY_LIBS = ["信贷政策", "审批规则"]


def _build(tmp_path: Path):
    store = KnowledgeStore(tmp_path / "kb.db")
    store.add_document(
        "信贷政策",
        "个人消费贷款基本准入",
        "月负债合计不超过月收入的 70%；优质客户征信评分 720 分以上。",
    )
    store.add_document(
        "反洗钱规则",
        "大额与可疑交易上报",
        "单笔达到 200 万元以上的贷款申请须履行反洗钱审查。",
    )
    tools.configure(store)
    graph = build_graph(
        model=MockChatModel(),
        checkpointer=InMemorySaver(),
        default_policy_libraries=POLICY_LIBS,
    )
    return graph, store


def _payload(applicant: dict, loan: dict, **extra):
    return {
        "application_id": 1,
        "operator_id": "tester",
        "applicant": applicant,
        "loan": {**loan, "annual_rate": 0.049},
        "materials": [],
        "policy_libraries": POLICY_LIBS,
        "review_type": "new",
        **extra,
    }


async def _run(graph, payload, config):
    final = None
    interrupts: list = []
    async for mode, chunk in graph.astream(
        payload, config, stream_mode=["values", "updates"]
    ):
        if mode == "values":
            final = chunk
        elif isinstance(chunk, dict) and "__interrupt__" in chunk:
            interrupts = list(chunk["__interrupt__"])
    return final, interrupts


LOW_APPLICANT = {
    "name": "张小雨",
    "monthly_income": 25000,
    "monthly_debt": 2000,
    "credit_score": 760,
    "industry": "信息技术",
}
MEDIUM_APPLICANT = {
    "name": "李建国",
    "monthly_income": 18000,
    "monthly_debt": 9000,
    "credit_score": 660,
    "industry": "批发零售",
}
HIGH_APPLICANT = {
    "name": "王强",
    "monthly_income": 40000,
    "monthly_debt": 5000,
    "credit_score": 600,
    "industry": "制造业",
}


async def test_low_risk_small_amount_auto_approved(tmp_path: Path) -> None:
    graph, _store = _build(tmp_path)
    config = {"configurable": {"thread_id": "auto"}}

    final, interrupts = await _run(
        graph, _payload(LOW_APPLICANT, {"amount": 60000, "term_months": 24}), config
    )

    assert interrupts == []
    assert final["risk_level"] == "low"
    assert final["route_level"] == "auto"
    assert final["decision"] == DECISION_AUTO_APPROVED
    # 报告正文里应包含政策依据（RAG 出处）
    report = "\n".join(str(m.content) for m in final["messages"])
    assert "初步风险评估报告" in report


async def test_medium_risk_interrupts_at_junior_reviewer(tmp_path: Path) -> None:
    graph, _store = _build(tmp_path)
    config = {"configurable": {"thread_id": "junior"}}

    _final, interrupts = await _run(
        graph, _payload(MEDIUM_APPLICANT, {"amount": 300000, "term_months": 36}), config
    )
    assert interrupts, "中风险申请应在人工审批处中断"
    payload = interrupts[0].value
    assert payload["level"] == LEVEL_JUNIOR
    assert payload["action"] == "approval_required"
    assert "risk_assessment" in payload


async def test_resume_approve_yields_approved(tmp_path: Path) -> None:
    graph, _store = _build(tmp_path)
    config = {"configurable": {"thread_id": "approve"}}

    await _run(
        graph, _payload(MEDIUM_APPLICANT, {"amount": 300000, "term_months": 36}), config
    )
    final, interrupts = await _run(
        graph,
        Command(resume={"action": "approve", "approver": "审批员甲", "comment": "同意"}),
        config,
    )

    assert interrupts == []
    assert final["decision"] == DECISION_APPROVED
    assert final["approval_records"][0]["approver"] == "审批员甲"
    assert any(entry["action"] == "approve" for entry in final["approval_chain"])


async def test_resume_reject_yields_rejected(tmp_path: Path) -> None:
    graph, _store = _build(tmp_path)
    config = {"configurable": {"thread_id": "reject"}}

    await _run(
        graph, _payload(MEDIUM_APPLICANT, {"amount": 300000, "term_months": 36}), config
    )
    final, _ = await _run(
        graph,
        Command(resume={"action": "reject", "approver": "审批员乙", "comment": "负债偏高"}),
        config,
    )
    assert final["decision"] == DECISION_REJECTED


async def test_resume_modify_merges_opinion(tmp_path: Path) -> None:
    graph, _store = _build(tmp_path)
    config = {"configurable": {"thread_id": "modify"}}

    await _run(
        graph, _payload(MEDIUM_APPLICANT, {"amount": 300000, "term_months": 36}), config
    )
    final, _ = await _run(
        graph,
        Command(
            resume={
                "action": "modify",
                "approver": "审批员丙",
                "comment": "降低额度",
                "modified_assessment": {
                    "risk_level": "low",
                    "recommendation": "额度下调至 20 万后放款",
                },
            }
        ),
        config,
    )
    assert final["decision"] == DECISION_CONDITIONAL
    assert final["risk_assessment"]["risk_level"] == "low"
    assert final["approval_records"][0]["modified"] is True


async def test_escalate_then_senior_approves(tmp_path: Path) -> None:
    graph, _store = _build(tmp_path)
    config = {"configurable": {"thread_id": "escalate"}}

    await _run(
        graph, _payload(MEDIUM_APPLICANT, {"amount": 300000, "term_months": 36}), config
    )
    # 初级审批人无法决定 → 升级
    _final, interrupts = await _run(
        graph,
        Command(resume={"action": "escalate", "approver": "初级甲", "comment": "超出权限"}),
        config,
    )
    assert interrupts and interrupts[0].value["level"] == LEVEL_SENIOR

    final, _ = await _run(
        graph,
        Command(resume={"action": "approve", "approver": "高级乙", "comment": "同意"}),
        config,
    )
    assert final["decision"] == DECISION_APPROVED
    assert any(entry["action"] == "escalate" for entry in final["approval_chain"])


async def test_high_risk_routes_to_senior(tmp_path: Path) -> None:
    graph, _store = _build(tmp_path)
    config = {"configurable": {"thread_id": "high"}}

    _final, interrupts = await _run(
        graph, _payload(HIGH_APPLICANT, {"amount": 80000, "term_months": 24}), config
    )
    assert interrupts and interrupts[0].value["level"] == LEVEL_SENIOR


async def test_aml_amount_routes_to_compliance(tmp_path: Path) -> None:
    graph, _store = _build(tmp_path)
    config = {"configurable": {"thread_id": "aml"}}

    _final, interrupts = await _run(
        graph, _payload(LOW_APPLICANT, {"amount": 2_500_000, "term_months": 60}), config
    )
    assert interrupts and interrupts[0].value["level"] == "compliance"


async def test_recheck_routes_to_senior_reviewer(tmp_path: Path) -> None:
    graph, _store = _build(tmp_path)
    config = {"configurable": {"thread_id": "recheck"}}

    _final, interrupts = await _run(
        graph,
        _payload(
            MEDIUM_APPLICANT,
            {"amount": 300000, "term_months": 36},
            review_type="recheck",
        ),
        config,
    )
    assert interrupts and interrupts[0].value["level"] == LEVEL_SENIOR
    assert interrupts[0].value["review_type"] == "recheck"
