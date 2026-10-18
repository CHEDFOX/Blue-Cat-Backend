"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DATABASE_URL = "postgresql+psycopg://ulis:ulis@localhost:5432/ulis"
# zstd level of the object store (ulis/storage/object_store.py). Measured on the whole eCFR object store (8,332 objects,
# 974.5 MB, each compressed on its own): level 3 x4.94 at 218 MB/s of CPU, 9 x5.70 at 50 MB/s, 12 x5.81 at 19 MB/s,
# 19 x6.23 at 1.9 MB/s. Level 9 keeps 98% of the bytes level 19 saves for 1/26 of its CPU; ingestion handles about
# 0.3 MB/s of raw documents per process, so compressing adds ~0.5% (docs/deployment.md section 13).
DEFAULT_OBJECT_COMPRESSION_LEVEL = 9
MAX_OBJECT_COMPRESSION_LEVEL = 22


@dataclass(frozen=True)
class Settings:
    database_url: str
    config_dir: Path
    object_store_dir: Path
    source_cache_dir: Path
    environment: str
    # Raw API keys accepted by the HTTP API. Empty means "no authentication",
    # which is only permitted when environment == "development".
    api_keys: frozenset[str]
    # Upper bound on candidates fetched from the index before reranking.
    retrieval_candidate_budget: int = 200
    # zstd level of the raw source documents in the object store (lossless; 0 = store them uncompressed).
    object_compression_level: int = DEFAULT_OBJECT_COMPRESSION_LEVEL

    @property
    def requires_api_key(self) -> bool:
        return bool(self.api_keys) or self.environment != "development"


def _split(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def _compression_level(value: str | None) -> int:
    if value is None or not value.strip():
        return DEFAULT_OBJECT_COMPRESSION_LEVEL
    try:
        level = int(value)
    except ValueError:
        raise ValueError(f"ULIS_OBJECT_COMPRESSION_LEVEL must be a whole number, got {value!r}") from None
    if not 0 <= level <= MAX_OBJECT_COMPRESSION_LEVEL:
        raise ValueError(
            f"ULIS_OBJECT_COMPRESSION_LEVEL must be 0 (uncompressed) to {MAX_OBJECT_COMPRESSION_LEVEL}, got {level}"
        )
    return level


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("ULIS_DATABASE_URL", DEFAULT_DATABASE_URL),
        config_dir=Path(os.environ.get("ULIS_CONFIG_DIR", REPO_ROOT / "config")),
        object_store_dir=Path(os.environ.get("ULIS_OBJECT_STORE_DIR", REPO_ROOT / "var" / "objects")),
        source_cache_dir=Path(os.environ.get("ULIS_SOURCE_CACHE_DIR", REPO_ROOT / "var" / "sources")),
        environment=os.environ.get("ULIS_ENV", "development"),
        api_keys=_split(os.environ.get("ULIS_API_KEYS")),
        retrieval_candidate_budget=int(os.environ.get("ULIS_RETRIEVAL_CANDIDATE_BUDGET", "200")),
        object_compression_level=_compression_level(os.environ.get("ULIS_OBJECT_COMPRESSION_LEVEL")),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
