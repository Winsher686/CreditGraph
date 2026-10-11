"""运行配置：全部来自环境变量，默认零成本（Mock 模型）。

设计原则：默认值必须让 `uv run creditgraph "..."` 开箱即用、不消耗任何 API
额度。接真实模型、或调整信贷规则阈值，只需设置环境变量（或项目根目录 `.env`，
已被 Git 忽略），不改代码——这就是"规则可配置"的落地方式。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    """加载项目根目录的 .env（不覆盖已有环境变量；文件不存在则跳过）。"""

    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    # config.py 位于 <root>/src/creditgraph/，向上两级是项目根。
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    # --- 模型 ---
    llm_provider: str = "mock"
    model: str = "credit-mock"
    model_small: str = ""
    model_large: str = ""
    openai_base_url: str = ""
    openai_api_key: str = ""
    max_output_tokens: int = 2048
    daily_token_budget: int = 0
    budget_exceeded_action: str = "block"
    # --- 检索 / Embedding ---
    embedding_provider: str = "mock"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 256
    default_policy_libraries: str = "信贷政策,审批规则,风险案例,监管要求,反洗钱规则"
    # --- 运行数据 ---
    database_path: str = "data/creditgraph.db"
    operator_id: str = "local"
    # --- 工作流 ---
    max_tool_rounds: int = 4
    tool_timeout_seconds: float = 10.0
    tool_max_retries: int = 2
    max_history_messages: int = 24
    max_upload_bytes: int = 8_000_000
    default_annual_rate: float = 0.049
    # --- 信贷规则阈值（可配置）---
    auto_approve_max_amount: float = 100_000.0
    senior_review_min_amount: float = 500_000.0
    aml_amount_threshold: float = 2_000_000.0
    max_dti: float = 0.70
    min_monthly_income: float = 3_000.0
    min_credit_score: int = 600
    approval_timeout_minutes: int = 120
    # 完整规则覆盖（JSON，优先级最高；用于覆盖上面未列出的字段）
    credit_rules_json: str = ""

    @property
    def policy_libraries(self) -> list[str]:
        return [item.strip() for item in self.default_policy_libraries.split(",") if item.strip()]

    def rule_overrides(self) -> dict:
        """汇总规则阈值覆盖值（含可选 JSON 全量覆盖）。"""

        overrides: dict = {
            "auto_approve_max_amount": self.auto_approve_max_amount,
            "senior_review_min_amount": self.senior_review_min_amount,
            "aml_amount_threshold": self.aml_amount_threshold,
            "max_dti": self.max_dti,
            "min_monthly_income": self.min_monthly_income,
            "min_credit_score": self.min_credit_score,
            "approval_timeout_minutes": self.approval_timeout_minutes,
        }
        raw = (self.credit_rules_json or "").strip()
        if raw:
            try:
                extra = json.loads(raw)
                if isinstance(extra, dict):
                    overrides.update(extra)
            except json.JSONDecodeError:
                pass
        return overrides


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def get_settings() -> Settings:
    return Settings(
        llm_provider=_env("CREDITGRAPH_LLM_PROVIDER", "mock").strip().lower(),
        model=_env("CREDITGRAPH_MODEL", "credit-mock"),
        model_small=_env("CREDITGRAPH_MODEL_SMALL", ""),
        model_large=_env("CREDITGRAPH_MODEL_LARGE", ""),
        openai_base_url=_env("CREDITGRAPH_OPENAI_BASE_URL", ""),
        openai_api_key=_env("CREDITGRAPH_OPENAI_API_KEY", ""),
        max_output_tokens=int(_env("CREDITGRAPH_MAX_OUTPUT_TOKENS", "2048")),
        daily_token_budget=int(_env("CREDITGRAPH_DAILY_TOKEN_BUDGET", "0")),
        budget_exceeded_action=_env("CREDITGRAPH_BUDGET_EXCEEDED_ACTION", "block"),
        embedding_provider=_env("CREDITGRAPH_EMBEDDING_PROVIDER", "mock").strip().lower(),
        embedding_model=_env("CREDITGRAPH_EMBEDDING_MODEL", "text-embedding-3-small"),
        embedding_dim=int(_env("CREDITGRAPH_EMBEDDING_DIM", "256")),
        default_policy_libraries=_env(
            "CREDITGRAPH_POLICY_LIBRARIES",
            "信贷政策,审批规则,风险案例,监管要求,反洗钱规则",
        ),
        database_path=_env("CREDITGRAPH_DATABASE_PATH", "data/creditgraph.db"),
        operator_id=_env("CREDITGRAPH_OPERATOR", "local"),
        max_tool_rounds=int(_env("CREDITGRAPH_MAX_TOOL_ROUNDS", "4")),
        tool_timeout_seconds=float(_env("CREDITGRAPH_TOOL_TIMEOUT_SECONDS", "10")),
        tool_max_retries=int(_env("CREDITGRAPH_TOOL_MAX_RETRIES", "2")),
        max_history_messages=int(_env("CREDITGRAPH_MAX_HISTORY_MESSAGES", "24")),
        max_upload_bytes=int(_env("CREDITGRAPH_MAX_UPLOAD_BYTES", "8000000")),
        default_annual_rate=float(_env("CREDITGRAPH_DEFAULT_ANNUAL_RATE", "0.049")),
        auto_approve_max_amount=float(
            _env("CREDITGRAPH_AUTO_APPROVE_MAX_AMOUNT", "100000")
        ),
        senior_review_min_amount=float(
            _env("CREDITGRAPH_SENIOR_REVIEW_MIN_AMOUNT", "500000")
        ),
        aml_amount_threshold=float(_env("CREDITGRAPH_AML_AMOUNT_THRESHOLD", "2000000")),
        max_dti=float(_env("CREDITGRAPH_MAX_DTI", "0.70")),
        min_monthly_income=float(_env("CREDITGRAPH_MIN_MONTHLY_INCOME", "3000")),
        min_credit_score=int(_env("CREDITGRAPH_MIN_CREDIT_SCORE", "600")),
        approval_timeout_minutes=int(
            _env("CREDITGRAPH_APPROVAL_TIMEOUT_MINUTES", "120")
        ),
        credit_rules_json=_env("CREDITGRAPH_RULES_JSON", ""),
    )
