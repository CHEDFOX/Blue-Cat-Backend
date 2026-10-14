"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DATABASE_URL = "postgresql+psycopg://ulis:ulis@localhost:5432/ulis"


@dataclass(frozen=True)
class Settings:
    database_url: str
    config_dir: Path
    object_store_dir: Path
    environment: str
    # Raw API keys accepted by the HTTP API. Empty means "no authentication",
    # which is only permitted when environment == "development".
    api_keys: frozenset[str]
    # Upper bound on candidates fetched from the index before reranking.
    retrieval_candidate_budget: int = 200

    @property
    def requires_api_key(self) -> bool:
        return bool(self.api_keys) or self.environment != "development"


def _split(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("ULIS_DATABASE_URL", DEFAULT_DATABASE_URL),
        config_dir=Path(os.environ.get("ULIS_CONFIG_DIR", REPO_ROOT / "config")),
        object_store_dir=Path(os.environ.get("ULIS_OBJECT_STORE_DIR", REPO_ROOT / "var" / "objects")),
        environment=os.environ.get("ULIS_ENV", "development"),
        api_keys=_split(os.environ.get("ULIS_API_KEYS")),
        retrieval_candidate_budget=int(os.environ.get("ULIS_RETRIEVAL_CANDIDATE_BUDGET", "200")),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
