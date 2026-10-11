# CreditGraph 前端

CreditGraph（LangGraph 金融信贷审批工作流）的 Web 界面，基于 **Next.js 16（App Router）+ TypeScript + Tailwind CSS v4**。

## 页面

| 路由 | 说明 |
| --- | --- |
| `/` | 审批总览：提交新申请、按状态筛选列表、一键播种示例数据 |
| `/applications/[id]` | 申请详情与审批台：风险评估报告（含政策出处）、审批链时间线、通过 / 拒绝 / 修改 / 升级操作、SSE 实时进度 |
| `/policies` | 政策知识库：信贷政策 / 审批规则 / 风险案例 / 监管要求 / 反洗钱规则的新增与检索 |
| `/rules` | 规则与审批矩阵：自动通过 / 高级审批 / 反洗钱 / 自动拒绝的阈值一览 |

## 本地运行

先启动后端（默认 Mock 模型，零 API 消耗）：

```bash
# 仓库根目录
uv sync
uv run uvicorn creditgraph.interfaces.api:app --port 8011 --reload
```

再启动前端：

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000
```

后端地址通过环境变量配置（默认 `http://127.0.0.1:8011`）：

```bash
cp .env.example .env.local
```

也可在仓库根目录执行 `./scripts/dev.sh` 同时拉起前后端。

## 代码结构

```
src/
├── app/                 页面（App Router）
├── components/          通用组件（AppShell 布局、ui 基础件）
└── lib/api.ts           后端接口封装（含 SSE 流式解析）
```

## 校验

```bash
npx tsc --noEmit      # 类型检查
npx eslint src        # 代码规范
npm run build         # 生产构建
```
