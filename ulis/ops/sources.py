"""What the operator needs to know about a source beyond its descriptor: size, politeness, refresh behaviour."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.registry import SourceDescriptor

# Sources whose manifest PINS exactly what is fetched (a commit, a release point, an explicit list of
# versions, a date, or hashes).  Re-running them yields nothing new until the manifest itself is
# edited or regenerated, so the weekly refresh skips them.  Every other source is "live": its
# manifest names works whose current text the publisher keeps changing, and a refresh re-fetches them.
STATIC_PINS: dict[str, str] = {
    "ulis-fictional-sample": "project-authored sample; never changes",
    "us-gpo-uslm-samples": "pinned git commit; regenerate with scripts/build_uslm_manifest.py",
    "ca-justice-laws-xml": "pinned commit and hashes; regenerate with scripts/build_justice_canada_manifest.py",
    "us-uscode-house": "pinned OLRC release point and per-title hashes; move the release point in the manifest",
    "ie-irish-statute-book": "Acts as enacted (hash-pinned); add new Acts to the manifest",
    "dk-retsinformation": "explicit list of consolidations; add newer ones to the manifest",
    "ee-riigi-teataja": "explicit list of versions (globaalID); add newer ones to the manifest",
    "ch-fedlex": "explicit list of consolidation files; regenerate with scripts/build_fedlex_manifest.py",
    "pl-eli-sejm": "explicit list of consolidated texts; add newer ones to the manifest",
    "it-normattiva": "text as of the date in the manifest; change the date to refresh",
    "eu-eur-lex": "explicit CELEX list (original Official Journal texts and the 2016 consolidated treaties)",
}

_URL_HOST = re.compile(r"https?://([A-Za-z0-9.-]+)")
DEFAULT_ENTRIES = 500  # ordering weight for a manifest that lists no recognisable collection


@dataclass(frozen=True)
class ManifestInfo:
    entries: int | None  # length of the manifest's main list (laws, works, documents, ...)
    requests: int | None  # lower bound of HTTP requests: entries x nested versions x languages
    crawl_delay: float | None  # seconds between requests, when the manifest sets one
    hosts: tuple[str, ...]  # hosts named in the manifest (they name the cache sub-directories)
    git: bool  # fetched with a git clone rather than HTTP requests

    @property
    def weight(self) -> int:
        return self.requests if self.requests is not None else DEFAULT_ENTRIES

    @property
    def min_network_seconds(self) -> float | None:
        """Lower bound of the download time: the request estimate at the crawl delay."""
        if self.git or self.requests is None or self.crawl_delay is None:
            return None
        return self.requests * self.crawl_delay


@lru_cache(maxsize=64)
def _read(path: str, mtime_ns: int) -> ManifestInfo:
    raw = Path(path).read_text(encoding="utf-8")
    loader: Any = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    data = yaml.load(raw, Loader=loader)  # noqa: S506 - a safe loader
    entries: int | None = None
    requests: int | None = None
    delay: float | None = None
    git = False
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        main = max(lists, key=len) if lists else None
        if main is not None:
            entries = len(main)
            # Entries that carry nested lists (consolidations, versions) need one request per element.
            nested = sum(
                max(1, sum(len(v) for v in item.values() if isinstance(v, list))) if isinstance(item, dict) else 1
                for item in main
            )
            languages = data.get("languages")
            requests = nested * (len(languages) if isinstance(languages, list) and languages else 1)
        raw_delay = data.get("crawl_delay_seconds")
        delay = float(raw_delay) if isinstance(raw_delay, int | float) else None
        git = "repository" in data
    hosts = tuple(sorted({h.lower().rstrip(".") for h in _URL_HOST.findall(raw)}))
    return ManifestInfo(entries, requests, delay, hosts, git)


def manifest_info(source: SourceDescriptor, base_dir: Path) -> ManifestInfo | None:
    endpoint = source.access.endpoint
    if not endpoint or endpoint.startswith(("http://", "https://")):
        return None
    path = base_dir / endpoint
    try:
        return _read(str(path), path.stat().st_mtime_ns)
    except (OSError, yaml.YAMLError):
        return None


def is_static(source_id: str) -> bool:
    return source_id in STATIC_PINS
