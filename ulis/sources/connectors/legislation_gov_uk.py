"""Polite point-in-time fetcher for legislation.gov.uk.

For every work in the manifest it fetches the current revised text and, if ``history`` is
set, the whole instrument as it stood on each date listed in its lifecycle - the dates on
which some provision changed - which yields every historical version.

Fair-use rules from legislation.gov.uk/fair-use-policy and robots.txt are built in:
an identifying User-Agent, the robots ``Crawl-delay`` (5 s) between network requests,
far below the 1,500 requests / 5 minutes limit, back-off on 429/5xx, and a local cache so
nothing is fetched twice.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError

_PATH = re.compile(r"^[a-z]+/\d{4}/\d+$")
_DATE = re.compile(rb'<FRBRExpression>.*?<FRBRdate date="(\d{4}-\d{2}-\d{2})', re.S)
_EVENTS = re.compile(rb'<eventRef[^>]*date="(\d{4}-\d{2}-\d{2})')


class FetchError(RuntimeError):
    pass


class LegislationGovUkConnector:
    def __init__(
        self,
        manifest_path: Path,
        expected_source_id: str,
        cache_dir: Path,
        *,
        offline: bool = False,
        sleep: Any = time.sleep,
    ):
        self.manifest_path = manifest_path
        self.expected_source_id = expected_source_id
        self.cache_dir = cache_dir / "legislation.gov.uk"
        self.offline = offline
        self._sleep = sleep
        self._last_request = 0.0
        self.requests_made = 0

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required (fair-use policy)")
        for work in manifest["works"]:
            if not _PATH.match(work["path"]):
                raise ManifestError(f"invalid work path {work['path']!r}")
        return manifest

    def _get(self, manifest: dict[str, Any], url: str, cache_file: Path) -> tuple[bytes, dict[str, Any]] | None:
        meta_file = cache_file.with_suffix(".json")
        if cache_file.exists() and meta_file.exists():
            return cache_file.read_bytes(), json.loads(meta_file.read_text())
        if self.offline:
            raise FetchError(f"offline and not cached: {url}")
        delay = float(manifest.get("crawl_delay_seconds", 5))
        for attempt in range(5):
            wait = self._last_request + delay - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last_request = time.monotonic()
            self.requests_made += 1
            if not url.startswith("https://"):
                raise FetchError(f"refusing non-https URL {url}")
            request = urllib.request.Request(url, headers={"User-Agent": manifest["user_agent"]})  # noqa: S310
            try:
                with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - https only
                    data = response.read()
                    meta = {
                        "url": url,
                        "final_url": response.geturl(),
                        "status": response.status,
                        "retrieved_at": datetime.now(UTC).isoformat(),
                    }
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                if exc.code in (429, 500, 502, 503, 504) and attempt < 4:
                    retry_after = exc.headers.get("Retry-After")
                    self._sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay * 2**attempt)
                    continue
                raise FetchError(f"{url}: HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < 4:
                    self._sleep(delay * 2**attempt)
                    continue
                raise FetchError(f"{url}: {exc}") from exc
        else:  # pragma: no cover - loop always breaks or raises
            raise FetchError(url)
        cache_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        cache_file.write_bytes(data)
        meta_file.write_text(json.dumps(meta))
        return data, meta

    def _raw(self, path: str, data: bytes, meta: dict[str, Any], version: str) -> RawDocument:
        return RawDocument(
            external_id=f"legislation.gov.uk:{path}@{version}",
            uri=meta.get("final_url") or meta["url"],
            media_type="application/akn+xml",
            content=data,
            retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
            metadata={"path": path, "point_in_time": version, "cached_fetch": meta},
        )

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        base = manifest.get("base_url", "https://www.legislation.gov.uk").rstrip("/")
        if not base.startswith("https://"):
            raise ManifestError("base_url must be https")
        for work in manifest["works"]:
            path = work["path"]
            current = self._get(manifest, f"{base}/{path}/data.akn", self.cache_dir / path / "current.akn")
            if current is None:
                raise FetchError(f"{path}: not found")
            data, meta = current
            match = _DATE.search(data)
            current_date = match.group(1).decode() if match else None
            if work.get("history", True) and current_date:
                seen = {current_date}
                # ``history_dates`` restricts the versions fetched (used for small test fixtures). A
                # partial history can hide intermediate versions, so production manifests omit it.
                wanted = work.get("history_dates")
                for d in sorted({m.decode() for m in _EVENTS.findall(data)}):
                    if d >= current_date or (wanted is not None and d not in {str(w) for w in wanted}):
                        continue
                    got = self._get(manifest, f"{base}/{path}/{d}/data.akn", self.cache_dir / path / f"{d}.akn")
                    if got is None:
                        continue
                    pit_match = _DATE.search(got[0])
                    pit_date = pit_match.group(1).decode() if pit_match else d
                    if pit_date in seen:  # this date resolved to a version already fetched
                        continue
                    seen.add(pit_date)
                    yield self._raw(path, got[0], got[1], pit_date)
            yield self._raw(path, data, meta, current_date or "current")
