"""端口（Ports）：应用层对外部世界的**抽象**。

洋葱架构的关键是"依赖倒置"——应用层不 import 具体实现（SQLite、OpenAI），
只依赖这里定义的 Protocol；具体实现在 `infrastructure/` 里提供。这样换数据库、
换模型、换检索实现都不需要改业务代码，测试时也可以塞假实现。
"""

from __future__ import annotations

from typing import Any, Protocol

from ..domain.models import Chunk, SearchHit


class KnowledgePort(Protocol):
    """信贷政策知识库：检索与写入（信贷政策 / 审批规则 / 风险案例）。"""

    def search(
        self, query: str, *, libraries: list[str] | None = None, limit: int = 4
    ) -> list[SearchHit]: ...

    def list_libraries(self) -> list[dict[str, object]]: ...

    def add_document(self, library: str, title: str, content: str) -> int: ...

    def list_chunks(self, *, library: str | None = None) -> list[Chunk]: ...


class CreditRepositoryPort(Protocol):
    """信贷审批的持久化：申请 / 风险评估 / 审批记录 / 审批链 / 审计日志。"""

    def create_application(
        self,
        *,
        applicant: dict[str, Any],
        loan: dict[str, Any],
        materials: list[dict[str, Any]] | None = None,
        status: str,
        created_at: float,
    ) -> int: ...

    def get_application(self, application_id: int) -> dict[str, Any] | None: ...

    def list_applications(
        self, *, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]: ...

    def update_application(
        self,
        application_id: int,
        *,
        status: str | None = None,
        decision: str | None = None,
        decision_reason: str | None = None,
        risk_level: str | None = None,
        review_type: str | None = None,
        updated_at: float,
    ) -> bool: ...

    def save_risk_assessment(
        self, application_id: int, *, assessment: dict[str, Any], created_at: float
    ) -> int: ...

    def list_risk_assessments(
        self, application_id: int
    ) -> list[dict[str, Any]]: ...

    def add_approval_record(
        self,
        application_id: int,
        *,
        approver: str,
        level: str,
        decision: str,
        comment: str,
        modified: bool,
        created_at: float,
    ) -> int: ...

    def list_approval_records(
        self, application_id: int
    ) -> list[dict[str, Any]]: ...

    def add_chain_entry(
        self,
        application_id: int,
        *,
        actor: str,
        action: str,
        from_level: str,
        to_level: str,
        detail: str,
        created_at: float,
    ) -> int: ...

    def list_chain(self, application_id: int) -> list[dict[str, Any]]: ...

    def add_audit(
        self,
        application_id: int,
        *,
        actor: str,
        action: str,
        detail: str,
        created_at: float,
    ) -> int: ...

    def list_audit(
        self, application_id: int, *, limit: int = 200
    ) -> list[dict[str, Any]]: ...


class UsageRepositoryPort(Protocol):
    """token 用量记账：写入每次模型调用、按日汇总。"""

    def record(
        self,
        *,
        user_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        created_at: float,
    ) -> None: ...

    def total_since(self, *, user_id: str, since: float) -> int: ...

    def summary(self, *, user_id: str, since: float) -> list[dict[str, Any]]: ...


class EmbeddingPort(Protocol):
    """向量化。"""

    @property
    def dim(self) -> int: ...

    @property
    def name(self) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class ChatModelPort(Protocol):
    """聊天模型（LangGraph 节点里调用）。"""

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any: ...

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any: ...
