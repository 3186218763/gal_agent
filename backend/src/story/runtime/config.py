"""OpenCode Go Responses configuration (secret-safe)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ConfigurationError(RuntimeError):
    pass


class OpenCodeGoSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["opencode_go"] = "opencode_go"
    api_key: SecretStr = Field(repr=False)
    base_url: str = "https://opencode.ai/zen/go/v1"
    model: str = "deepseek-v4-flash"
    api: Literal["responses"] = "responses"
    timeout_seconds: float = Field(default=45, gt=0, le=300)
    max_retries: int = Field(default=1, ge=0, le=2)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        localhost = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if not parsed.hostname or (parsed.scheme != "https" and not localhost):
            raise ValueError("base_url must use HTTPS except for localhost tests")
        return value.rstrip("/")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> OpenCodeGoSettings:
        env = os.environ if environ is None else environ
        provider = env.get("GAL_LLM_PROVIDER")
        if provider != "opencode_go":
            raise ConfigurationError("GAL_LLM_PROVIDER must be opencode_go")
        primary = env.get("OPENCODE_GO_API_KEY")
        alias = env.get("OPENAI_API_KEY")
        if primary and alias and primary != alias:
            raise ConfigurationError(
                "OPENCODE_GO_API_KEY and OPENAI_API_KEY are both set with different values"
            )
        key = primary or alias
        if not key:
            raise ConfigurationError("OPENCODE_GO_API_KEY is required")
        api = env.get("GAL_LLM_API", "responses")
        if api != "responses":
            raise ConfigurationError("GAL_LLM_API must be responses")
        return cls(
            provider=provider,
            api_key=SecretStr(key),
            base_url=env.get("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1").rstrip(
                "/"
            ),
            model=env.get("GAL_LLM_MODEL", "deepseek-v4-flash"),
            api=api,
            timeout_seconds=float(env.get("GAL_LLM_TIMEOUT_SECONDS", "45")),
            max_retries=int(env.get("GAL_LLM_MAX_RETRIES", "1")),
        )
