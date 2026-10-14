"""Assistant configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MODEL_DEEP = "claude-opus-5-5"

MAX_QUESTION_CHARS = 4000
MAX_CASE_CHARS = 60000
# Raw HTTP body ceiling: 60k characters can be ~720 kB when a client escapes every character as \uXXXX.
MAX_BODY_BYTES = 1_000_000


def _int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, int(raw))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _float(name: str, default: float, *, minimum: float = 0.01) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, float(raw))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True)
class AssistantConfig:
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    model_deep: str = DEFAULT_MODEL_DEEP
    max_searches_standard: int = 12
    max_searches_deep: int = 30
    # Total output tokens one job may spend over all of its model calls (standard); deep gets twice as much.
    max_output_tokens: int = 48000
    ttl_hours: float = 24.0
    # How many jobs run at once in this process, and how many a caller may create / have active.
    workers: int = 4
    rate_per_hour: int = 30
    max_active_per_principal: int = 3
    # "default" opts in to the API's server-side refusal fallback on models that support it; "off" disables.
    fallbacks: str = "default"
    # "auto" sends a per-stage effort level; "off" omits it (for models that do not accept the parameter).
    effort: str = "auto"
    request_timeout_s: float = 600.0

    @property
    def llm_configured(self) -> bool:
        return bool(self.api_key)

    def model_for(self, depth: str) -> str:
        return self.model_deep if depth == "deep" else self.model

    def search_cap(self, depth: str) -> int:
        return (
            max(self.max_searches_standard, self.max_searches_deep) if depth == "deep" else self.max_searches_standard
        )

    def output_budget(self, depth: str) -> int:
        return self.max_output_tokens * (2 if depth == "deep" else 1)


def load_assistant_config() -> AssistantConfig:
    standard = _int("ULIS_RESEARCH_MAX_SEARCHES", 12)
    return AssistantConfig(
        api_key=(os.environ.get("ANTHROPIC_API_KEY") or "").strip() or None,
        model=os.environ.get("ULIS_LLM_MODEL", "").strip() or DEFAULT_MODEL,
        model_deep=os.environ.get("ULIS_LLM_MODEL_DEEP", "").strip() or DEFAULT_MODEL_DEEP,
        max_searches_standard=standard,
        max_searches_deep=max(standard, _int("ULIS_RESEARCH_MAX_SEARCHES_DEEP", 30)),
        max_output_tokens=_int("ULIS_RESEARCH_MAX_OUTPUT_TOKENS", 48000, minimum=4000),
        ttl_hours=_float("ULIS_RESEARCH_TTL_HOURS", 24.0),
        workers=_int("ULIS_RESEARCH_WORKERS", 4),
        rate_per_hour=_int("ULIS_RESEARCH_RATE_PER_HOUR", 30),
        max_active_per_principal=_int("ULIS_RESEARCH_MAX_ACTIVE", 3),
        fallbacks=(os.environ.get("ULIS_LLM_FALLBACKS", "default").strip().lower() or "default"),
        effort=(os.environ.get("ULIS_LLM_EFFORT", "auto").strip().lower() or "auto"),
        request_timeout_s=_float("ULIS_LLM_TIMEOUT_S", 600.0, minimum=5.0),
    )
