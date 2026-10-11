# 接口一览

Base URL：`http://127.0.0.1:8011`（可用 `NEXT_PUBLIC_API_BASE` 在前端覆盖）

## 健康与规则

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 服务状态、模型 provider、当前规则阈值 |
| GET | `/api/rules` | 审批矩阵表格 + 规则说明 + 阈值 |

## 申请

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/applications` | 提交贷款申请 |
| GET | `/api/applications` | 申请列表（`?status=` 过滤，`?limit=` 限制条数） |
| GET | `/api/applications/{id}` | 申请状态 + 风险评估报告 + 审批链 + 审批记录 |
| POST | `/api/applications/{id}/stream` | **跑审批工作流（SSE）** |
| POST | `/api/applications/{id}/resume` | HITL 恢复（通用，body 指定 action） |
| POST | `/api/applications/{id}/approve` | 审批通过 |
| POST | `/api/applications/{id}/reject` | 审批拒绝 |
| POST | `/api/applications/{id}/modify` | 审批人修改风险评估后提交 |
| POST | `/api/applications/{id}/escalate` | 升级到高级审批人 |
| POST | `/api/applications/{id}/recheck` | 对已通过贷款发起复审 / 抽查 |
| GET | `/api/applications/{id}/audit-trail` | 完整审批链 + 审批记录 + 审计日志 |

### 请求体：`POST /api/applications`

```json
{
  "applicant": {
    "name": "李建国", "age": 41, "occupation": "个体经营者", "industry": "批发零售",
    "monthly_income": 18000, "monthly_debt": 9000, "credit_score": 660,
    "blacklisted": false, "related_party": false
  },
  "loan": { "amount": 300000, "term_months": 36, "purpose": "店面装修" },
  "materials": [
    { "name": "收入证明", "text": "月收入 18000 元", "kind": "text" },
    { "name": "征信报告", "text": "信用评分 660", "kind": "text" }
  ],
  "operator": "applicant"
}
```

响应：`{"application": {...}}`；随后调用 `/stream` 触发工作流。

### 请求体：`POST /api/applications/{id}/(approve|reject|modify|escalate|resume)`

```json
{
  "approver": "审批员甲",
  "comment": "同意 / 拒绝原因 / 修改说明",
  "modified_assessment": { "risk_level": "low", "recommendation": "额度下调至 20 万后放款" }
}
```

- `approve` / `reject` / `escalate`：`modified_assessment` 可省略。
- `modify`：`modified_assessment` 会合并进最终风险结论（修改回流）。
- `resume`：需在 body 里额外传 `action`（`approve|reject|modify|escalate`）。

## 政策知识库

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/policies/libraries` | 政策库列表（含文档数） |
| GET | `/api/policies/documents?library=信贷政策` | 某库文档列表 |
| POST | `/api/policies` | 新增政策 / 规则 / 案例文档 |
| POST | `/api/policies/upload` | 上传文件（TXT / MD / PDF / 图片）入库 |

## 用量

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/usage?user_id=local&days=1` | token 用量（按模型 / 按日） |

## SSE 事件协议

`/stream` 与各审批动作返回 `text/event-stream`，每个事件一行 `event:` + 一行 `data:`（JSON）：

| 事件 | data | 说明 |
| --- | --- | --- |
| `status` | `{"label": "生成初步风险评估报告"}` | 当前节点 |
| `step` | `{"kind":"stage","title":"...","detail":"..."}` | 节点说明 |
| `token` | `{"content": "..."}` | 报告正文逐块推送 |
| `interrupt` | `{"action":"approval_required","level":"junior","risk_assessment":{...},"options":[...]}` | **HITL 中断**，需审批人决策 |
| `decision` | `{"decision":"approved","status":"approved","risk_level":"low"}` | 终局决策 |
| `done` | `{"application": {...}, "interrupted": false}` | 本次流结束 |
| `error` | `{"message": "..."}` | 失败（不中断连接） |

> 客户端在收到 `interrupt` 后调用对应审批动作端点；`done` 表示该阶段（可能是中断也可能是终局）结束。

## 命令行

```bash
uv run creditgraph rules [--json]
uv run creditgraph new --name 张三 --income 20000 --debt 3000 --score 720 \
    --amount 300000 --term 36 --purpose 装修 [--auto]
uv run creditgraph list
uv run creditgraph show <id>
uv run creditgraph audit <id>
```
