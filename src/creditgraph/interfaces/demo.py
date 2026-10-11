"""示例数据播种：信贷政策知识库 + 不同风险等级的示例申请。

    uv run creditgraph-demo                # 写入默认数据库
    uv run creditgraph-demo --db demo.db   # 写到指定数据库
    uv run creditgraph-demo --reset        # 先清空再写入

知识库内容为**自编示例政策与案例**（非任何监管机构的正式文本），用于演示
RAG 检索与出处标注；示例申请覆盖低 / 中 / 高三个风险档与反洗钱触发场景。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from ..application.credit_service import CreditService
from ..config import Settings, get_settings
from ..infrastructure.credit_repository import SqliteCreditRepository
from ..infrastructure.embeddings import get_embedder
from ..infrastructure.knowledge import KnowledgeStore

# 政策库 -> [(标题, 正文)]
DEMO_POLICIES: dict[str, list[tuple[str, str]]] = {
    "信贷政策": [
        (
            "个人消费贷款基本准入",
            "【准入】申请人年龄 22-60 周岁、有稳定收入来源；"
            "【额度】单笔消费贷款不超过月收入的 24 倍；"
            "【期限】最长 60 个月。",
        ),
        (
            "收入与负债认定口径",
            "【收入】以近 6 个月银行流水均值认定；"
            "【负债】月负债合计不超过月收入的 70%；"
            "【优质客户】征信评分 720 分以上且负债率低于 50%。",
        ),
    ],
    "审批规则": [
        (
            "分级审批矩阵",
            "低风险且金额低于 10 万元可自动通过；"
            "中风险或金额 10-50 万元由初级审批人确认；"
            "高风险或金额超过 50 万元须高级审批人审批。",
        ),
        (
            "自动拒绝清单",
            "命中征信黑名单、月收入低于当地最低标准、"
            "负债率超过 70% 的申请直接拒绝，不进入人工环节。",
        ),
    ],
    "风险案例": [
        (
            "案例：多头借贷导致的逾期",
            "某客户同时申请 5 家机构贷款，征信报告显示近 3 个月查询次数 12 次，"
            "最终因月供总额超过收入 90% 而逾期。启示：负债率与查询次数须交叉验证。",
        ),
        (
            "案例：经营贷资金流入楼市",
            "某小微经营贷放款后资金流向房地产，违反用途约定，被要求提前收回。"
            "启示：贷后须核查资金用途，必要时启动复审。",
        ),
    ],
    "监管要求": [
        (
            "贷款资金用途管理",
            "贷款资金不得用于购房、股票、期货等投资性用途；"
            "放款后应进行用途核查与贷后跟踪。",
        ),
        (
            "关联交易与集中度",
            "对关联方授信须履行合规审查程序，关注单一客户集中度指标。",
        ),
    ],
    "反洗钱规则": [
        (
            "大额与可疑交易上报",
            "单笔或累计达到 200 万元以上的贷款申请须履行反洗钱审查；"
            "用途为「现金周转」「代他人借款」等应列为可疑。",
        ),
        (
            "受限行业清单",
            "虚拟货币、博彩、典当、民间借贷、房地产开发等行业申请须转合规部门审批。",
        ),
    ],
}

# 示例申请：覆盖三档风险 + 反洗钱触发
DEMO_APPLICATIONS: list[dict] = [
    {
        "applicant": {
            "name": "张小雨",
            "age": 29,
            "occupation": "软件工程师",
            "industry": "信息技术",
            "monthly_income": 25000,
            "monthly_debt": 2000,
            "credit_score": 760,
        },
        "loan": {"amount": 60000, "term_months": 24, "purpose": "家电购置"},
        "materials": [
            {"name": "收入证明", "text": "月收入 25000 元，任职于某科技公司。", "kind": "text"},
            {"name": "征信报告", "text": "信用评分 760，无逾期记录。", "kind": "text"},
            {"name": "资产负债表", "text": "月负债 2000 元（房贷）。", "kind": "text"},
        ],
    },
    {
        "applicant": {
            "name": "李建国",
            "age": 41,
            "occupation": "个体经营者",
            "industry": "批发零售",
            "monthly_income": 18000,
            "monthly_debt": 9000,
            "credit_score": 660,
        },
        "loan": {"amount": 300000, "term_months": 36, "purpose": "店面装修"},
        "materials": [
            {"name": "收入证明", "text": "近 6 个月月均流水 18000 元。", "kind": "text"},
            {"name": "征信报告", "text": "信用评分 660，近半年查询 5 次。", "kind": "text"},
            {"name": "资产负债表", "text": "月负债 9000 元（含他行贷款）。", "kind": "text"},
        ],
    },
    {
        "applicant": {
            "name": "王强",
            "age": 35,
            "occupation": "自由职业",
            "industry": "房地产开发",
            "monthly_income": 40000,
            "monthly_debt": 5000,
            "credit_score": 700,
            "related_party": True,
        },
        "loan": {"amount": 2500000, "term_months": 60, "purpose": "现金周转"},
        "materials": [
            {"name": "收入证明", "text": "月收入 40000 元。", "kind": "text"},
            {"name": "征信报告", "text": "信用评分 700。", "kind": "text"},
        ],
    },
    {
        "applicant": {
            "name": "赵敏",
            "age": 52,
            "occupation": "无固定职业",
            "industry": "",
            "monthly_income": 2000,
            "monthly_debt": 1500,
            "credit_score": 580,
            "blacklisted": True,
        },
        "loan": {"amount": 100000, "term_months": 24, "purpose": "个人周转"},
        "materials": [
            {"name": "征信报告", "text": "命中征信黑名单。", "kind": "text"},
        ],
    },
]


def seed(settings: Settings, *, operator: str = "demo") -> dict[str, object]:
    knowledge = KnowledgeStore(settings.database_path, embedder=get_embedder(settings))
    repository = SqliteCreditRepository(settings.database_path)
    service = CreditService(repository, knowledge)

    policy_count = 0
    for library, documents in DEMO_POLICIES.items():
        for title, content in documents:
            knowledge.add_document(library, title, content)
            policy_count += 1

    application_ids: list[int] = []
    for item in DEMO_APPLICATIONS:
        record = service.submit(
            applicant=item["applicant"],
            loan={**item["loan"], "annual_rate": settings.default_annual_rate},
            materials=item["materials"],
            operator=operator,
        )
        application_ids.append(int(record["id"]))

    return {
        "libraries": list(DEMO_POLICIES),
        "policies": policy_count,
        "applications": application_ids,
        "database": settings.database_path,
    }


def _reset_database(path: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        target = Path(f"{path}{suffix}")
        if target.exists():
            target.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="creditgraph-demo", description="播种示例数据")
    parser.add_argument("--db", default=None, help="数据库路径（默认读环境变量）")
    parser.add_argument("--user", default="demo", help="操作人标识")
    parser.add_argument("--reset", action="store_true", help="先删除数据库再播种")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.db:
        settings = replace(settings, database_path=args.db)
    if args.reset:
        _reset_database(settings.database_path)

    result = seed(settings, operator=args.user)
    print("示例数据已写入：")
    print(f"  数据库：{result['database']}")
    print(f"  政策知识库：{'、'.join(result['libraries'])}")  # type: ignore[arg-type]
    print(f"  政策 / 案例文档：{result['policies']} 份")
    print(f"  示例申请：{len(result['applications'])} 笔 → id {result['applications']}")
    print("  提示：对每笔申请调用 POST /api/applications/{id}/stream 即可跑审批工作流。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
