# CreditGraph

**用 [LangGraph](https://langchain-ai.github.io/langgraph/) 编排的金融信贷审批工作流**——把「材料接收与解析 → 初步风险评估 → 规则引擎 → 审批路由 → 人工审批(HITL) → 意见合并 → 最终决策 → 贷后复审」串成一条可中断、可恢复、可审计的闭环。

CreditGraph 面向银行 / 消费金融 / 小贷机构的授信审批场景：申请人提交材料后，系统调用大模型生成初
步风险评估报告（含信用评分、还款能力分析、风险等级），规则引擎判定自动拒绝 / 自动通过 / 转人工 /
反洗钱合规，审批矩阵按「风险等级 × 金额 × 合规」路由到初级 / 高级审批人 / 合规部门；审批人可确认、
修改、拒绝或升级，每一级的操作、时间、意见与决策都落库形成**完整审批链**。它把 LangGraph 的
**状态机、检查点、Human-in-the-Loop（`interrupt()`）与流式输出**用在真实的信贷审批流程上。

> 技术栈：Python 3.12+ · FastAPI · LangGraph · AsyncSqliteSaver · SQLite FTS5 + 向量 + RRF · SSE · Next.js 16

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 🧭 多步骤决策 | 一笔申请是一条显式状态图：`intake → risk_assessment → rule_engine → approval_route → human_approval → merge_opinion → final_decision`，分支与循环都写在明面上 |
| 🤖 AI 风险评估 | 检索信贷政策 / 审批规则 / 风险案例（**带出处**），调用大模型生成评分与风险等级；解析失败自动回退确定性口径，流程不中断 |
| ⚖️ 规则引擎 | 自动拒绝（征信黑名单 / 收入不达标 / 负债率过高）、自动通过（低风险 + 小额 + 优质客户）、转人工、合规检查（反洗钱 / 关联交易 / 行业限制）；**阈值全部可配置** |
| 🧮 审批矩阵 | 低风险 + 金额 < 10 万 → 自动通过；中风险 + 10–50 万 → 初级审批人；高风险或金额 > 50 万 → 高级审批人；触发反洗钱 → 合规部门 |
| ✋ Human-in-the-Loop | 在人工审批节点 `interrupt()` 暂停，审批人可「通过 / 拒绝 / 修改风险评估后提交 / 升级」；`AsyncSqliteSaver` 持久化审批状态，中断后可恢复 |
| ⬆️ 升级机制 | 初级审批人无法决定时逐级升级：初级 → 高级 → 合规，升级后重新中断等待审批 |
| 🔗 审批链与审计 | `approval_chain` 记录每一级流转（from → to + 意见 + 时间），`audit_logs` 记录所有操作，支持合规审计与 `GET /audit-trail` 一键导出 |
| 🔁 贷后复审 | 对已审批通过的贷款发起复审 / 抽查，重新评估后由高级审批人核定「维持 / 关注 / 提前收回」 |
| 🔎 混合检索 RAG | SQLite FTS5 **trigram** 中文检索 + **向量余弦相似度**，两路用 **RRF** 融合；结果带出处，便于审批人核实 |
| 🌊 流式输出 | SSE：`status` / `step` / `token` / `interrupt` / `decision` / `done` / `error`，实时推送节点进展与报告生成过程 |
| 🛡️ 护栏 Guardrails | 输入：拦越界与提示词注入（含「绕过审批 / 改成通过」等）；输出：拦空报告与系统提示词泄漏 |
| 💾 断点持久化 | `AsyncSqliteSaver` 检查点：工作流可续、HITL 可恢复 |
| 🤖 双模型适配 | 默认确定性 Mock（零成本，覆盖三档风险 + 合规场景），可切任意 OpenAI 兼容平台 |

## 审批流程

📄 **[审批流程图（PDF）](docs/assets/approval-flow.pdf)** — `intake → risk_assessment → rule_engine → approval_route`，
在 `approval_route` 按「自动终局 / 需人工」分流；人工节点支持升级回环与意见合并回流，最终由 `final_decision` 收口。

> 另有 **[审批状态图（PDF）](docs/assets/approval-state.pdf)**（架构设计用）。两张图均由
> `scripts/generate_diagrams.py` 生成矢量 PDF，可随时重新生成。

**HITL 中断恢复机制**：`human_approval` 节点内的 `interrupt(payload)` 会抛出 `GraphInterrupt`，
图在这一步暂停并把 `payload`（风险评估报告 + 路由信息 + 可选动作）交给前端；`AsyncSqliteSaver`
把这一刻的状态写进检查点。前端调用 `/approve|/reject|/modify|/escalate`（内部是 `Command(resume=...)`）
后，图从**同一线程**恢复，`interrupt()` 的返回值即审批人的意见对象，随后进入 `merge_opinion` 与
`final_decision`。断开进程也不丢状态——重启后凭 `thread_id` 即可继续。

## 审批矩阵（默认阈值，可配置）

| 条件 | 审批路径 |
| --- | --- |
| 自动拒绝规则命中（黑名单 / 收入不达标 / 负债率 > 70%） | 自动拒绝 |
| 低风险 且 金额 < 10 万 | 自动通过 |
| 中风险 且 金额 10–50 万 | 初级审批人确认 |
| 高风险 或 金额 > 50 万 | 高级审批人审批 |
| 金额 ≥ 200 万 / 可疑用途 / 受限行业 / 关联交易 | 合规部门审批 |

阈值通过 `.env`（`CREDITGRAPH_*`）或 `CREDITGRAPH_RULES_JSON` 覆盖，**改配置不用改代码**。

## 分层架构（洋葱 / 六边形）

代码按**依赖只朝内**分层，外部技术可替换而不动业务规则：

| 层 | 目录 | 允许依赖 | 内容 |
| --- | --- | --- | --- |
| 领域 Domain | `domain/` | 仅标准库 | 风险等级 / 决策枚举、**规则引擎**、**审批矩阵**、负债率与月供计算、RRF 融合、护栏规则 |
| 应用 Application | `application/` | 领域 + 端口 | 材料解析、规则编排、审批用例、**LangGraph 状态图与工具**、端口 Protocol |
| 基础设施 Infrastructure | `infrastructure/` | 应用 / 领域 | SQLite 审批仓储（6 张表）、政策知识库与混合检索、Embedding、LLM |
| 接口 Interfaces | `interfaces/` | 全部 | FastAPI + SSE、CLI（**组合根**）、示例数据播种 |

- 应用层只依赖 `application/ports.py` 里的 Protocol（`KnowledgePort` / `CreditRepositoryPort` /
  `UsageRepositoryPort` / `EmbeddingPort` / `ChatModelPort`），不 import 任何具体实现。
- 图（`application/graph.py`）保持**纯**——不写数据库；落库由接口层在一次工作流跑完后统一执行，
  避免与 `AsyncSqliteSaver` 争同一 SQLite 文件的写锁。

## 快速开始

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync

# 查看审批矩阵与规则阈值
uv run creditgraph rules

# 提交一笔申请并跑完整审批工作流（遇到人工审批时在终端交互）
uv run creditgraph new --name 李建国 --income 18000 --debt 9000 --score 660 \
    --amount 300000 --term 36 --purpose 店面装修
# 加 --auto 可自动同意所有审批动作（演示用）

uv run creditgraph list          # 申请列表
uv run creditgraph show 1        # 申请详情（含风险评估报告）
uv run creditgraph audit 1       # 完整审批链 + 审计日志
```

### 播种示例数据

```bash
uv run creditgraph-demo --reset
```

写入 5 个政策知识库（信贷政策 / 审批规则 / 风险案例 / 监管要求 / 反洗钱规则，共 10 份自编文档）
与 4 笔覆盖不同风险档的示例申请。`--reset` 会**清空并重建**数据库。

### 启动 Web 界面（推荐）

```bash
cd frontend && npm install && cd ..   # 首次
./scripts/dev.sh                       # 后端 8011 + 前端 3000
```

打开 <http://localhost:3000>：**审批总览**（提交申请 / 申请列表）→ 点击「进入审批台」→
「运行审批工作流」实时查看节点进展与风险评估报告 → 中断时选择审批动作 → 查看审批链与审计日志。

## 接入真实模型

任何 OpenAI 兼容平台（DeepSeek / OpenAI / …），配置写进项目根目录 `.env`（Git 忽略，启动时自动加载）：

```bash
uv sync --extra openai
# .env 示例（DeepSeek）
# CREDITGRAPH_LLM_PROVIDER=openai
# CREDITGRAPH_MODEL=deepseek-flash
# CREDITGRAPH_OPENAI_BASE_URL=https://api.deepseek.com/v1
# CREDITGRAPH_OPENAI_API_KEY=你的密钥
# CREDITGRAPH_MAX_OUTPUT_TOKENS=4096
```

环境变量清单见 [.env.example](.env.example)。推理型模型务必设 `CREDITGRAPH_MAX_OUTPUT_TOKENS=4096`。

## 测试

```bash
uv run pytest -q --basetemp=.pytest-tmp   # 69 passed（本机沙箱须指定 basetemp）
uv run ruff check src tests scripts
```

覆盖：规则引擎（自动拒绝 / 自动通过 / 合规）、审批矩阵与升级链、状态图（自动终局 / HITL 中断 /
通过 / 拒绝 / 修改回流 / 升级 / 复审）、FastAPI 接口与 SSE、审批链与审计落库。全部跑在确定性 Mock 上，**零 API 消耗**。

前端质量门禁：

```bash
cd frontend && npx tsc --noEmit && npx eslint src
```

## 项目结构

```text
creditgraph/
├── src/creditgraph/
│   ├── domain/            纯领域逻辑（不依赖框架 / DB）
│   │   ├── credit.py         风险等级/决策枚举 · 规则引擎 · 审批矩阵 · 超时与升级
│   │   ├── credit_prompts.py 风险评估 / 意见合并 / 复审 提示词模板
│   │   ├── models.py         Applicant / LoanTerms / RiskAssessment 等
│   │   ├── retrieval.py      切块 + RRF 融合
│   │   ├── guardrails.py     输入/输出护栏规则
│   │   └── model_routing.py  按审批环节选模型档位
│   ├── application/       用例编排 + 端口（LangGraph 在这层）
│   │   ├── ports.py          对外部世界的 Protocol
│   │   ├── state.py          CreditState（审批流水线状态）
│   │   ├── graph.py          信贷审批状态图 + HITL 中断
│   │   ├── routing.py        审批角色与工具最小权限
│   │   ├── tools.py          policy_search / risk_calc / calculator
│   │   ├── credit_rules.py   规则引擎编排（阈值可配置）
│   │   ├── credit_service.py 审批用例（落库 / 审批链 / 审计 / 复审）
│   │   └── material_analysis.py 材料接收与解析
│   ├── infrastructure/    适配器（端口的实现）
│   │   ├── credit_repository.py  applications / risk_assessments / approval_records
│   │   │                         / approval_chain / audit_logs
│   │   ├── knowledge.py          政策库：SQLite + FTS5 + 向量 + RRF
│   │   ├── embeddings.py / extract.py / usage_repository.py
│   │   └── llm.py                Mock（信贷规则）+ OpenAI 兼容
│   ├── interfaces/        入口（组合根）
│   │   ├── api.py            FastAPI + SSE（全部端点）
│   │   ├── cli.py            命令行
│   │   └── demo.py           示例数据播种
│   └── config.py
├── frontend/          Next.js 16 界面（审批总览 / 审批台 / 政策库 / 规则）
├── tests/             69 个自动测试（Mock，零 API 消耗）
├── scripts/dev.sh     一键起前后端
├── data/              SQLite 库（git 忽略）
└── docs/              架构设计 / 接口一览 / 使用指南
```

## 文档

- [架构设计](docs/架构设计.md)：图结构、状态模型、审批矩阵、HITL 恢复机制、分层与取舍
- [接口一览](docs/参考/API一览.md)：全部端点与 SSE 协议
- [使用指南](docs/使用指南.md)：安装、示例、常见问题
