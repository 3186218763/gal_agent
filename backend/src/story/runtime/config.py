"""LLM provider configuration (secret-safe)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ConfigurationError(RuntimeError):
    pass


ProviderId = Literal["opencode_go", "everygpt"]
ApiFlavor = Literal["responses", "chat_completions"]


@dataclass(frozen=True)
class _ProviderSpec:
    api: ApiFlavor
    key_env: str
    base_url_env: str
    base_url: str
    model: str


_PROVIDER_SPECS: dict[str, _ProviderSpec] = {
    "opencode_go": _ProviderSpec(
        api="responses",
        key_env="OPENCODE_GO_API_KEY",
        base_url_env="OPENCODE_GO_BASE_URL",
        base_url="https://opencode.ai/zen/go/v1",
        model="deepseek-v4-flash",
    ),
    "everygpt": _ProviderSpec(
        api="chat_completions",
        key_env="EVERYGPT_API_KEY",
        base_url_env="EVERYGPT_BASE_URL",
        base_url="https://api.everygpt.site/v1",
        model="gemini-3.7-flash",
    ),
}


class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderId
    api_key: SecretStr = Field(repr=False)
    base_url: str
    model: str
    api: ApiFlavor
    timeout_seconds: float | None = Field(default=45, gt=0, le=3600)
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
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LLMSettings:
        env = os.environ if environ is None else environ
        provider = env.get("GAL_LLM_PROVIDER")
        spec = _PROVIDER_SPECS.get(provider) if provider else None
        if spec is None:
            allowed = ", ".join(sorted(_PROVIDER_SPECS))
            raise ConfigurationError(f"GAL_LLM_PROVIDER must be one of: {allowed}")
        primary = env.get(spec.key_env)
        alias = env.get("OPENAI_API_KEY")
        if primary and alias and primary != alias:
            raise ConfigurationError(
                f"{spec.key_env} and OPENAI_API_KEY are both set with different values"
            )
        key = primary or alias
        if not key:
            raise ConfigurationError(f"{spec.key_env} is required")
        api = env.get("GAL_LLM_API", spec.api)
        if api != spec.api:
            raise ConfigurationError(f"GAL_LLM_API must be {spec.api} for provider {provider}")
        return cls(
            provider=provider,
            api_key=SecretStr(key),
            base_url=env.get(spec.base_url_env, spec.base_url).rstrip("/"),
            model=env.get("GAL_LLM_MODEL", spec.model),
            api=api,
            timeout_seconds=_parse_timeout(env.get("GAL_LLM_TIMEOUT_SECONDS") or "45"),
            max_retries=int(env.get("GAL_LLM_MAX_RETRIES", "1")),
        )


def _parse_timeout(raw: str) -> float | None:
    """Parse ``GAL_LLM_TIMEOUT_SECONDS``; ``0`` (or ``none``) means no timeout."""
    value = raw.strip().lower()
    if value in {"0", "0.0", "none"}:
        return None
    return float(value)
