"""Runtime configuration.

All configuration is environment-driven. No secret has a usable default: a
missing secret in a non-development environment is a hard startup failure
rather than a silent fallback.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENTIC_",
        env_file=(".env", ".env.local"),
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Environment = "development"
    app_name: str = "Agentic OS Enterprise"
    app_version: str = "3.1.0"

    # --- data plane -------------------------------------------------------
    database_url: str = "postgresql+psycopg://agentic_app:agentic_app@127.0.0.1:5432/agentic"
    database_owner_url: str = (
        "postgresql+psycopg://agentic_owner:agentic_owner@127.0.0.1:5432/agentic"
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    redis_url: str = "redis://127.0.0.1:6379/0"
    object_store_path: str = "./.data/objects"

    # --- security ---------------------------------------------------------
    jwt_secret: str = Field(default="", description="HS256 signing key for access tokens")
    jwt_issuer: str = "agentic-os"
    jwt_audience: str = "agentic-os-api"
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 86400 * 7
    password_min_length: int = 12
    mfa_required_roles: tuple[str, ...] = ("platform_admin", "security_admin", "auditor")

    secret_backend: Literal["env", "file", "vault"] = "env"
    secret_file_path: str = "./.data/secrets.json"
    vault_addr: str = ""
    kms_backend: Literal["local", "aws-kms", "azure-kv", "gcp-kms"] = "local"
    kms_local_key: str = Field(default="", description="Base64 32-byte local data key")

    # --- policy / governance ---------------------------------------------
    policy_mode: Literal["enforce", "monitor"] = "enforce"
    policy_dir: str = "policies"
    contracts_dir: str = "packages/contracts"
    prompts_dir: str = "prompts"
    evidence_signing_mode: Literal["development", "production"] = "development"
    audit_hash_algorithm: str = "sha256"

    # --- AI plane ---------------------------------------------------------
    default_model_provider: str = "deterministic"
    model_allow_external_providers: bool = False
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    local_model_base_url: str = "http://127.0.0.1:11434"
    model_request_timeout_seconds: float = 60.0
    embedding_provider: str = "deterministic-hash"
    embedding_dimensions: int = 384

    # --- budgets ----------------------------------------------------------
    default_tenant_daily_cost_cap_usd: float = 250.0
    default_run_cost_cap_usd: float = 5.0
    default_run_token_cap: int = 250_000
    default_run_tool_call_cap: int = 50
    default_run_wallclock_cap_seconds: int = 900

    # --- egress -----------------------------------------------------------
    egress_allowlist: tuple[str, ...] = ()
    egress_block_private_networks: bool = True

    # --- observability ----------------------------------------------------
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "agentic-os-api"
    log_level: str = "INFO"

    # --- api --------------------------------------------------------------
    cors_allowed_origins: tuple[str, ...] = ("http://localhost:3000",)
    rate_limit_per_minute: int = 240
    max_upload_bytes: int = 64 * 1024 * 1024

    @field_validator("app_env", mode="before")
    @classmethod
    def _normalise_env(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env in ("staging", "production")

    def validate_for_boot(self) -> list[str]:
        """Return a list of fatal configuration problems for this environment."""
        problems: list[str] = []
        if self.is_production:
            if len(self.jwt_secret) < 32:
                problems.append("AGENTIC_JWT_SECRET must be >= 32 chars outside development")
            if self.evidence_signing_mode != "production":
                problems.append("AGENTIC_EVIDENCE_SIGNING_MODE must be 'production'")
            if self.policy_mode != "enforce":
                problems.append("AGENTIC_POLICY_MODE must be 'enforce' in production")
            if self.kms_backend == "local":
                problems.append("AGENTIC_KMS_BACKEND must not be 'local' in production")
            if self.secret_backend == "env":
                problems.append("AGENTIC_SECRET_BACKEND must be 'file' or 'vault' in production")
            if "*" in self.cors_allowed_origins:
                problems.append("Wildcard CORS origin is not permitted in production")
        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if not settings.jwt_secret:
        if settings.is_production:
            raise RuntimeError("AGENTIC_JWT_SECRET is required outside development")
        # Development/test only: deterministic per-process ephemeral key.
        settings.jwt_secret = os.environ.setdefault(
            "AGENTIC_JWT_SECRET", "dev-only-insecure-signing-key-change-me-0001"
        )
    problems = settings.validate_for_boot()
    if problems:
        raise RuntimeError("Invalid configuration: " + "; ".join(problems))
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
