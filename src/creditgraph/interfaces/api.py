"""接口层：HTTP。这里是**组合根**——把基础设施实现注入应用层服务与图。

启动：`uv run uvicorn creditgraph.interfaces.api:app --port 8011 --reload`

信贷审批接口一览：

    POST   /api/applications                     提交贷款申请
    GET    /api/applications                     申请列表（可按 status 过滤）
    GET    /api/applications/{id}                申请状态 + 风险评估报告
    POST   /api/applications/{id}/stream         跑工作流（SSE 实时推送进度与报告）
    POST   /api/applications/{id}/resume         HITL 恢复（审批人动作）
    POST   /api/applications/{id}/approve        审批通过
    POST   /api/applications/{id}/reject         审批拒绝
    POST   /api/applications/{id}/modify         审批人修改风险评估后提交
    POST   /api/applications/{id}/escalate       升级到高级审批人
    POST   /api/applications/{id}/recheck        对已通过贷款发起复审 / 抽查
    GET    /api/applications/{id}/audit-trail    完整审批链 + 审计日志
    GET    /api/rules                            审批矩阵与规则阈值
    GET/POST /api/policies/...                   信贷政策知识库

SSE 事件协议（每行一个事件，`data` 为 JSON）：

    event: status     data: {"label": "生成风险评估报告"}     # 节点进展
    event: step       data: {"kind": "stage", "title": "...", "detail": "..."}
    event: token      data: {"content": "..."}                # 报告逐字推送
    event: interrupt  data: {"action": "approval_required", ...}  # HITL 中断
    event: decision   data: {"decision": "approved", ...}
    event: done       data: {"application": {...}}
    event: error      data: {"message": "..."}
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from ..application import tools
from ..application.credit_rules import describe, load_rules
from ..application.credit_service import STATUS_APPROVED, CreditService
from ..application.graph import build_graph
from ..application.material_analysis import (
    MaterialError,
    material_image_to_text,
)
from ..application.routing import (
    ACTION_APPROVE,
    ACTION_ESCALATE,
    ACTION_MODIFY,
    ACTION_REJECT,
)
from ..config import Settings, get_settings
from ..domain.guardrails import check_input
from ..infrastructure.credit_repository import SqliteCreditRepository
from ..infrastructure.embeddings import get_embedder
from ..infrastructure.extract import ExtractError, extract_text, is_image
from ..infrastructure.knowledge import KnowledgeStore
from ..infrastructure.llm import build_chat_model
from ..infrastructure.usage_repository import SqliteUsageRepository

# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------


class ApplicantModel(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    id_number: str = Field(default="", max_length=64)
    age: int = Field(default=0, ge=0, le=120)
    occupation: str = Field(default="", max_length=64)
    industry: str = Field(default="", max_length=64)
    monthly_income: float = Field(default=0.0, ge=0)
    monthly_debt: float = Field(default=0.0, ge=0)
    credit_score: int = Field(default=0, ge=0, le=1000)
    blacklisted: bool = False
    related_party: bool = False


class LoanModel(BaseModel):
    amount: float = Field(gt=0, description="贷款金额（元）")
    term_months: int = Field(default=12, ge=1, le=360)
    purpose: str = Field(default="", max_length=200)
    annual_rate: float = Field(default=0.0, ge=0, le=1)


class MaterialModel(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    text: str = Field(default="", max_length=200000)
    kind: str = Field(default="text", max_length=16)


class ApplicationRequest(BaseModel):
    applicant: ApplicantModel
    loan: LoanModel
    materials: list[MaterialModel] = Field(default_factory=list)
    policy_libraries: list[str] = Field(default_factory=list)
    operator: str = Field(default="applicant", max_length=64)


class ApprovalActionRequest(BaseModel):
    approver: str = Field(default="reviewer", max_length=64)
    comment: str = Field(default="", max_length=2000)
    modified_assessment: dict[str, Any] | None = None


class PolicyRequest(BaseModel):
    library: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200000)


# ---------------------------------------------------------------------------
# SSE 辅助
# ---------------------------------------------------------------------------

_NODE_LABELS = {
    "intake": "接收并解析申请材料",
    "risk_assessment": "生成初步风险评估报告",
    "rule_engine": "执行信贷规则引擎",
    "approval_route": "按审批矩阵路由",
    "human_approval": "等待人工审批",
    "escalate": "升级审批层级",
    "merge_opinion": "合并审批意见",
    "final_decision": "生成最终审批决策",
}

_STAGE_DETAIL = {
    "intake": "解析收入证明 / 征信报告 / 资产负债表",
    "risk_assessment": "检索政策依据并调用大模型评分",
    "rule_engine": "自动拒绝 / 自动通过 / 转人工 / 反洗钱",
    "approval_route": "风险等级 × 金额 → 审批层级",
    "human_approval": "HITL 中断，等待审批人确认 / 修改 / 拒绝 / 升级",
    "escalate": "初级 → 高级 → 合规",
    "merge_opinion": "审批人意见优先于 AI 原结论",
    "final_decision": "通过 / 拒绝 / 有条件通过",
}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _chunk_text(text: str, *, size: int = 6) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or []


def _node_messages(update: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for message in update.get("messages", []) or []:
        content = str(getattr(message, "content", "") or "")
        if content.strip():
            texts.append(content)
    return texts


def _step_for(node: str, update: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "stage",
        "title": _NODE_LABELS.get(node, node),
        "detail": _STAGE_DETAIL.get(node, ""),
    }


async def _stream_graph(
    graph,
    payload: Any,
    config: dict,
    *,
    service: CreditService,
    application_id: int,
):
    """跑图并转成 SSE 流（只用 ``updates`` 模式，避免模型原始 JSON 外泄）。"""

    interrupted = False
    try:
        async for chunk in graph.astream(payload, config, stream_mode="updates"):
            if not isinstance(chunk, dict):
                continue
            if "__interrupt__" in chunk:
                interrupted = True
                value = chunk["__interrupt__"][0].value
                await asyncio.to_thread(
                    service.mark_interrupted,
                    application_id,
                    level=str(value.get("level") or ""),
                )
                yield _sse("interrupt", value)
                continue
            for node, update in chunk.items():
                if not isinstance(update, dict):
                    continue
                yield _sse("status", {"label": _NODE_LABELS.get(node, node)})
                yield _sse("step", _step_for(node, update))
                for text in _node_messages(update):
                    for piece in _chunk_text(text):
                        yield _sse("token", {"content": piece})

        if interrupted:
            record = await asyncio.to_thread(service.get, application_id)
            yield _sse("done", {"application": record, "interrupted": True})
            return

        state = await graph.aget_state(config)
        values = dict(state.values) if state is not None else {}
        record = await asyncio.to_thread(service.persist_run, application_id, values)
        yield _sse(
            "decision",
            {
                "decision": record.get("decision"),
                "status": record.get("status"),
                "risk_level": record.get("risk_level"),
            },
        )
        yield _sse("done", {"application": record})
    except Exception as exc:  # noqa: BLE001 - 把失败作为事件返回，而不是断连
        yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})


def _sse_headers() -> dict[str, str]:
    return {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved = settings or get_settings()
        knowledge = KnowledgeStore(
            resolved.database_path, embedder=get_embedder(resolved)
        )
        repository = SqliteCreditRepository(resolved.database_path)
        usage_repository = SqliteUsageRepository(resolved.database_path)
        service = CreditService(repository, knowledge)
        rules = load_rules(resolved)
        tools.configure(knowledge)

        model = build_chat_model(resolved)
        tier_models: dict[str, object] = {}

        def model_router(tier: str):
            from dataclasses import replace

            name = {
                "small": resolved.model_small,
                "large": resolved.model_large,
            }.get(tier, "")
            if not name:
                return model
            if tier not in tier_models:
                tier_models[tier] = build_chat_model(replace(resolved, model=name))
            return tier_models[tier]

        async with AsyncSqliteSaver.from_conn_string(resolved.database_path) as checkpointer:
            app.state.settings = resolved
            app.state.store = knowledge
            app.state.repository = repository
            app.state.service = service
            app.state.rules = rules
            app.state.model = model
            app.state.usage = usage_repository
            app.state.graph = build_graph(
                model=model,
                checkpointer=checkpointer,
                rules=rules,
                default_policy_libraries=resolved.policy_libraries,
                default_annual_rate=resolved.default_annual_rate,
                max_tool_rounds=resolved.max_tool_rounds,
                tool_timeout=resolved.tool_timeout_seconds,
                tool_retries=resolved.tool_max_retries,
                max_history_messages=resolved.max_history_messages,
                model_router=model_router,
                usage_repository=usage_repository,
                daily_token_budget=resolved.daily_token_budget,
                budget_exceeded_action=resolved.budget_exceeded_action,
            )
            yield

    app = FastAPI(title="CreditGraph API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- 健康与规则 -----------------------------------------------------------

    @app.get("/api/health")
    async def health(request: Request) -> dict:
        settings_ = request.app.state.settings
        return {
            "status": "ok",
            "provider": settings_.llm_provider,
            "rules": describe(request.app.state.rules)["thresholds"],
        }

    @app.get("/api/rules")
    async def rules_info(request: Request) -> dict:
        return describe(request.app.state.rules)

    # -- 申请 -----------------------------------------------------------------

    @app.post("/api/applications")
    async def create_application(body: ApplicationRequest, request: Request) -> dict:
        """提交贷款申请：解析材料 → 落库 → 返回申请记录（接着调 /stream 跑工作流）。"""

        combined = " ".join([m.text for m in body.materials] + [body.loan.purpose])
        guard = check_input(combined[:4000])
        if not guard.allowed:
            raise HTTPException(status_code=400, detail=f"材料被安全策略拦截：{guard.reason}")

        service: CreditService = request.app.state.service
        record = service.submit(
            applicant=body.applicant.model_dump(),
            loan={**body.loan.model_dump(), "annual_rate": body.loan.annual_rate
                  or request.app.state.settings.default_annual_rate},
            materials=[m.model_dump() for m in body.materials],
            operator=body.operator,
        )
        return {"application": record}

    @app.get("/api/applications")
    async def list_applications(
        request: Request,
        status: str | None = Query(default=None, max_length=32),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict:
        service: CreditService = request.app.state.service
        return {"applications": service.list(status=status, limit=limit)}

    @app.get("/api/applications/{application_id}")
    async def get_application(application_id: int, request: Request) -> dict:
        service: CreditService = request.app.state.service
        record = service.get(application_id)
        if record is None:
            raise HTTPException(status_code=404, detail="申请不存在")
        return {"application": record}

    @app.post("/api/applications/{application_id}/stream")
    async def stream_application(application_id: int, request: Request) -> StreamingResponse:
        """跑信贷审批工作流（SSE）：材料解析 → 风险评估 → 规则 → 路由 → 审批。"""

        service: CreditService = request.app.state.service
        record = service.get(application_id)
        if record is None:
            raise HTTPException(status_code=404, detail="申请不存在")

        loan = dict(record["loan"])
        loan.setdefault(
            "annual_rate", request.app.state.settings.default_annual_rate
        )
        payload = {
            "application_id": application_id,
            "operator_id": request.app.state.settings.operator_id,
            "applicant": record["applicant"],
            "loan": loan,
            "materials": record.get("materials") or [],
            "policy_libraries": request.app.state.settings.policy_libraries,
            "review_type": "new",
        }
        config = {"configurable": {"thread_id": f"app-{application_id}"}}
        await asyncio.to_thread(service.mark_in_review, application_id, stage="intake")
        return StreamingResponse(
            _stream_graph(
                request.app.state.graph,
                payload,
                config,
                service=service,
                application_id=application_id,
            ),
            media_type="text/event-stream",
            headers=_sse_headers(),
        )

    async def _resume(
        application_id: int, request: Request, opinion: dict[str, Any]
    ) -> StreamingResponse:
        service: CreditService = request.app.state.service
        if service.get(application_id) is None:
            raise HTTPException(status_code=404, detail="申请不存在")
        config = {"configurable": {"thread_id": f"app-{application_id}"}}
        return StreamingResponse(
            _stream_graph(
                request.app.state.graph,
                Command(resume=opinion),
                config,
                service=service,
                application_id=application_id,
            ),
            media_type="text/event-stream",
            headers=_sse_headers(),
        )

    @app.post("/api/applications/{application_id}/resume")
    async def resume_application(
        application_id: int, body: ApprovalActionRequest, request: Request
    ) -> StreamingResponse:
        opinion = {
            "action": ACTION_APPROVE,
            "approver": body.approver,
            "comment": body.comment,
            "modified_assessment": body.modified_assessment,
        }
        return await _resume(application_id, request, opinion)

    @app.post("/api/applications/{application_id}/approve")
    async def approve_application(
        application_id: int, body: ApprovalActionRequest, request: Request
    ) -> StreamingResponse:
        return await _resume(
            application_id,
            request,
            {
                "action": ACTION_APPROVE,
                "approver": body.approver,
                "comment": body.comment,
                "modified_assessment": body.modified_assessment,
            },
        )

    @app.post("/api/applications/{application_id}/reject")
    async def reject_application(
        application_id: int, body: ApprovalActionRequest, request: Request
    ) -> StreamingResponse:
        return await _resume(
            application_id,
            request,
            {
                "action": ACTION_REJECT,
                "approver": body.approver,
                "comment": body.comment,
                "modified_assessment": body.modified_assessment,
            },
        )

    @app.post("/api/applications/{application_id}/modify")
    async def modify_application(
        application_id: int, body: ApprovalActionRequest, request: Request
    ) -> StreamingResponse:
        """审批人修改 AI 风险评估内容后重新提交（修改回流）。"""

        return await _resume(
            application_id,
            request,
            {
                "action": ACTION_MODIFY,
                "approver": body.approver,
                "comment": body.comment,
                "modified_assessment": body.modified_assessment or {},
            },
        )

    @app.post("/api/applications/{application_id}/escalate")
    async def escalate_application(
        application_id: int, body: ApprovalActionRequest, request: Request
    ) -> StreamingResponse:
        return await _resume(
            application_id,
            request,
            {
                "action": ACTION_ESCALATE,
                "approver": body.approver,
                "comment": body.comment or "升级到高级审批人",
                "modified_assessment": body.modified_assessment,
            },
        )

    @app.post("/api/applications/{application_id}/recheck")
    async def recheck_application(
        application_id: int,
        request: Request,
        operator: str = Query(default="risk_control", max_length=64),
    ) -> StreamingResponse:
        """对已审批通过的贷款发起复审 / 抽查（重新走评估 → 高级审批人核定）。"""

        service: CreditService = request.app.state.service
        record = service.get(application_id)
        if record is None:
            raise HTTPException(status_code=404, detail="申请不存在")
        if record["status"] != STATUS_APPROVED:
            raise HTTPException(status_code=400, detail="只有已审批通过的贷款才能复审")

        service.start_recheck(application_id, operator=operator)
        loan = dict(record["loan"])
        loan.setdefault("annual_rate", request.app.state.settings.default_annual_rate)
        payload = {
            "application_id": application_id,
            "operator_id": operator,
            "applicant": record["applicant"],
            "loan": loan,
            "materials": record.get("materials") or [],
            "policy_libraries": request.app.state.settings.policy_libraries,
            "review_type": "recheck",
        }
        config = {
            "configurable": {
                "thread_id": f"app-{application_id}-recheck-{int(time.time())}"
            }
        }
        return StreamingResponse(
            _stream_graph(
                request.app.state.graph,
                payload,
                config,
                service=service,
                application_id=application_id,
            ),
            media_type="text/event-stream",
            headers=_sse_headers(),
        )

    @app.get("/api/applications/{application_id}/audit-trail")
    async def audit_trail(application_id: int, request: Request) -> dict:
        service: CreditService = request.app.state.service
        if service.get(application_id) is None:
            raise HTTPException(status_code=404, detail="申请不存在")
        return service.audit_trail(application_id)

    # -- 政策知识库 -----------------------------------------------------------

    @app.get("/api/policies/libraries")
    async def policy_libraries(request: Request) -> dict:
        service: CreditService = request.app.state.service
        return {"libraries": service.list_policy_libraries()}

    @app.get("/api/policies/documents")
    async def policy_documents(
        request: Request, library: str = Query(min_length=1, max_length=64)
    ) -> dict:
        return {"documents": request.app.state.store.list_documents(library)}

    @app.post("/api/policies")
    async def add_policy(body: PolicyRequest, request: Request) -> dict:
        service: CreditService = request.app.state.service
        document_id = service.add_policy(body.library, body.title, body.content)
        return {"id": document_id, "library": body.library, "title": body.title}

    @app.post("/api/policies/upload")
    async def upload_policy(
        request: Request,
        library: str = Form(min_length=1, max_length=64),
        file: UploadFile = File(...),  # noqa: B008 — FastAPI 的标准依赖注入写法
    ) -> dict:
        data = await file.read()
        if len(data) > request.app.state.settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="文件太大")
        title = (file.filename or "上传政策").strip()
        if is_image(file.filename or ""):
            try:
                text = await material_image_to_text(
                    request.app.state.model, file.filename or "", data
                )
            except MaterialError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
        else:
            try:
                text = extract_text(file.filename or "", data)
            except ExtractError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not text.strip():
            raise HTTPException(status_code=400, detail="文件里没有可提取的文本")
        document_id = request.app.state.store.add_document(library, title, text)
        return {"id": document_id, "library": library, "title": title, "chars": len(text)}

    # -- 用量 -----------------------------------------------------------------

    @app.get("/api/usage")
    async def usage(request: Request, user_id: str = "local", days: int = 1) -> dict:
        since = time.time() - max(1, days) * 86400
        repository = request.app.state.usage
        return {
            "total_tokens": repository.total_since(user_id=user_id, since=since),
            "by_model": repository.summary(user_id=user_id, since=since),
            "daily_token_series": repository.daily_series(user_id=user_id, days=7),
        }

    return app


app = create_app()
