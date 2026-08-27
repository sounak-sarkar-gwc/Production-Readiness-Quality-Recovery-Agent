"""Centralized, validated configuration. Read once at import time so a
misconfigured deployment fails fast and loudly instead of surfacing as a
mysterious 500 on the first request that happens to need the missing value.
"""

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _split_csv_env(name: str, default: List[str]) -> List[str]:
    raw = os.environ.get(name)
    if not raw:
        return default
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@dataclass(frozen=True)
class Settings:
    env: str = os.environ.get("ENV", "development")
    port: int = int(os.environ.get("PORT", "8100"))

    gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")
    agent_model: str = os.environ.get("AGENT_MODEL", "gemini-3.6-flash")
    agent_timeout_seconds: float = float(os.environ.get("AGENT_TIMEOUT_SECONDS", "30"))
    agent_max_output_tokens: int = int(os.environ.get("AGENT_MAX_OUTPUT_TOKENS", "1500"))
    agent_max_retries: int = int(os.environ.get("AGENT_MAX_RETRIES", "3"))

    # Blank = auth disabled (local/dev default). Set this to require an
    # X-API-Key header on every /prq/* route except the health probes.
    backend_api_key: str = os.environ.get("BACKEND_API_KEY", "")

    cors_origins: List[str] = field(
        default_factory=lambda: _split_csv_env(
            "CORS_ORIGINS", ["http://localhost:3000", "http://localhost:5173"]
        )
    )

    rate_limit_default: str = os.environ.get("RATE_LIMIT_DEFAULT", "120/minute")
    # Tighter limit on the two LLM-backed endpoints -- these cost real
    # tokens against a free-tier quota, unlike the deterministic reads.
    rate_limit_llm: str = os.environ.get("RATE_LIMIT_LLM", "20/minute")

    log_level: str = os.environ.get("LOG_LEVEL", "INFO")

    # "csv" (default) reads the local datasets; "supabase" fetches the same
    # tables from a Supabase project instead. See supabase_schema.sql and
    # migrate_to_supabase.py.
    data_source: str = os.environ.get("DATA_SOURCE", "csv")
    supabase_url: str = os.environ.get("SUPABASE_URL", "")
    supabase_key: str = os.environ.get("SUPABASE_KEY", "")

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    @property
    def auth_enabled(self) -> bool:
        return bool(self.backend_api_key)

    @property
    def agent_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def using_supabase(self) -> bool:
        return self.data_source.lower() == "supabase"


settings = Settings()
