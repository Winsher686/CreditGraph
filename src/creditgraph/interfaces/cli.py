"""接口层：CLI。组合根，把基础设施注入应用层。

用法：

    uv run creditgraph new --name 张三 --income 20000 --debt 3000 --score 750 \
        --amount 80000 --term 24 --purpose 装修
    uv run creditgraph list
    uv run creditgraph show 1
    uv run creditgraph audit 1

`new` 会跑完整条审批工作流：遇到人工审批（HITL）时在终端里询问审批动作
（approve / reject / modify / escalate），支持"断点续跑"——中断后可用
`--thread` 恢复。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from ..application import tools
from ..application.credit_rules import load_rules
from ..application.credit_service import CreditService
from ..application.graph import build_graph
from ..application.routing import (
    ACTION_APPROVE,
    ACTION_LABELS,
    APPROVAL_ACTIONS,
)
from ..config import Settings, get_settings
from ..domain.credit import LEVEL_LABELS
from ..infrastructure.credit_repository import SqliteCreditRepository
from ..infrastructure.embeddings import get_embedder
from ..infrastructure.knowledge import KnowledgeStore
from ..infrastructure.llm import build_chat_model
from ..infrastructure.usage_repository import SqliteUsageRepository


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="creditgraph", description="信贷审批 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="提交一笔贷款申请并跑审批工作流")
    new.add_argument("--name", required=True, help="申请人姓名")
    new.add_argument("--income", type=float, default=0.0, help="月收入（元）")
    new.add_argument("--debt", type=float, default=0.0, help="月负债（元）")
    new.add_argument("--score", type=int, default=0, help="征信评分")
    new.add_argument("--amount", type=float, required=True, help="贷款金额（元）")
    new.add_argument("--term", type=int, default=12, help="期限（月）")
    new.add_argument("--purpose", default="", help="贷款用途")
    new.add_argument("--occupation", default="", help="职业")
    new.add_argument("--industry", default="", help="所属行业")
    new.add_argument("--blacklisted", action="store_true", help="是否命中征信黑名单")
    new.add_argument("--related-party", action="store_true", help="是否存在关联交易")
    new.add_argument("--auto", action="store_true", help="自动同意所有审批动作")

    sub.add_parser("list", help="列出所有申请")
    show = sub.add_parser("show", help="查看某笔申请的状态与风险评估报告")
    show.add_argument("application_id", type=int)
    audit = sub.add_parser("audit", help="查看某笔申请的完整审批链与审计日志")
    audit.add_argument("application_id", type=int)
    rules = sub.add_parser("rules", help="打印审批矩阵与规则阈值")
    rules.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _build(settings: Settings):
    knowledge = KnowledgeStore(settings.database_path, embedder=get_embedder(settings))
    repository = SqliteCreditRepository(settings.database_path)
    usage_repository = SqliteUsageRepository(settings.database_path)
    service = CreditService(repository, knowledge)
    tools.configure(knowledge)
    rules = load_rules(settings)
    model = build_chat_model(settings)

    def model_router(tier: str):
        name = {"small": settings.model_small, "large": settings.model_large}.get(tier, "")
        return build_chat_model(replace(settings, model=name)) if name else model

    return knowledge, repository, usage_repository, service, rules, model, model_router


async def _run_new(settings: Settings, args: argparse.Namespace) -> int:
    (
        _knowledge,
        _repository,
        usage_repository,
        service,
        rules,
        model,
        model_router,
    ) = _build(settings)

    record = service.submit(
        applicant={
            "name": args.name,
            "occupation": args.occupation,
            "industry": args.industry,
            "monthly_income": args.income,
            "monthly_debt": args.debt,
            "credit_score": args.score,
            "blacklisted": args.blacklisted,
            "related_party": args.related_party,
        },
        loan={
            "amount": args.amount,
            "term_months": args.term,
            "purpose": args.purpose,
            "annual_rate": settings.default_annual_rate,
        },
        materials=[],
        operator="cli",
    )
    application_id = int(record["id"])
    thread = f"app-{application_id}"
    config = {"configurable": {"thread_id": thread}}
    print(f"已提交申请 #{application_id}（金额 {args.amount:.0f} 元）")

    async with AsyncSqliteSaver.from_conn_string(settings.database_path) as checkpointer:
        graph = build_graph(
            model=model,
            checkpointer=checkpointer,
            rules=rules,
            default_policy_libraries=settings.policy_libraries,
            default_annual_rate=settings.default_annual_rate,
            model_router=model_router,
            usage_repository=usage_repository,
            daily_token_budget=settings.daily_token_budget,
            budget_exceeded_action=settings.budget_exceeded_action,
        )
        payload: object = {
            "application_id": application_id,
            "operator_id": "cli",
            "applicant": record["applicant"],
            "loan": record["loan"],
            "materials": [],
            "policy_libraries": settings.policy_libraries,
            "review_type": "new",
        }
        while True:
            interrupted = None
            async for chunk in graph.astream(payload, config, stream_mode="updates"):
                if not isinstance(chunk, dict):
                    continue
                if "__interrupt__" in chunk:
                    interrupted = chunk["__interrupt__"][0].value
                    continue
                for _node, update in chunk.items():
                    if isinstance(update, dict):
                        for message in update.get("messages", []) or []:
                            content = str(getattr(message, "content", "") or "")
                            if content.strip():
                                print(content)

            if not interrupted:
                break

            opinion = _ask_approval(interrupted, auto=args.auto)
            payload = Command(resume=opinion)

        state = await graph.aget_state(config)
        final = service.persist_run(application_id, dict(state.values))
        print(
            f"\n最终决策：{final['decision']}（状态 {final['status']}）"
            f" 风险等级：{final.get('risk_level')}"
        )
    return 0


def _ask_approval(payload: dict, *, auto: bool) -> dict:
    level = str(payload.get("level") or "")
    print(
        f"\n[HITL 中断] 等待{LEVEL_LABELS.get(level, level)}审批"
        f"（原因：{payload.get('route_reason', '')}）"
    )
    actions = " / ".join(f"{a}={ACTION_LABELS[a]}" for a in APPROVAL_ACTIONS)
    print(f"可选动作：{actions}")
    if auto:
        return {"action": ACTION_APPROVE, "approver": "cli-auto", "comment": "自动同意"}
    raw = input("请输入动作 [approve]: ").strip().lower() or ACTION_APPROVE
    if raw not in APPROVAL_ACTIONS:
        raw = ACTION_APPROVE
    comment = input("审批意见（可空）: ").strip()
    return {"action": raw, "approver": "cli", "comment": comment}


def _print_rules(settings: Settings, as_json: bool) -> int:
    from ..application.credit_rules import describe

    rules = load_rules(settings)
    info = describe(rules)
    if as_json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(info["text"])
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    if args.command == "rules":
        return _print_rules(settings, args.json)
    if args.command == "new":
        return asyncio.run(_run_new(settings, args))
    if args.command == "list":
        _k, repository, *_ = _build(settings)
        for row in repository.list_applications(limit=50):
            print(
                f"#{row['id']} {row['applicant'].get('name')} "
                f"金额 {row['loan'].get('amount')} 状态 {row['status']} "
                f"决策 {row['decision'] or '-'} 风险 {row['risk_level'] or '-'}"
            )
        return 0
    if args.command == "show":
        _k, _r, _u, service, *_ = _build(settings)
        record = service.get(args.application_id)
        if record is None:
            print("申请不存在")
            return 1
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0
    if args.command == "audit":
        _k, _r, _u, service, *_ = _build(settings)
        trail = service.audit_trail(args.application_id)
        print("审批链：")
        for entry in trail["chain"]:
            print(
                f"  [{entry['seq']}] {entry['actor']} {entry['action']} "
                f"{entry['from_level']}→{entry['to_level']} | {entry['detail']}"
            )
        print("审计日志：")
        for log in trail["audits"]:
            print(f"  [{log['action']}] {log['actor']} | {log['detail']}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
