"""Polite point-in-time fetcher for legislation.gov.uk.

For every work in the manifest it fetches the current revised text and, if ``history`` is
set, the whole instrument as it stood on each date listed in its lifecycle - the dates on
which some provision changed - which yields every historical version.

Fair-use rules from legislation.gov.uk/fair-use-policy and robots.txt are built in:
an identifying User-Agent, the robots ``Crawl-delay`` (5 s) between network requests,
far below the 1,500 requests / 5 minutes limit, back-off on 429/5xx, and a local cache so
nothing is fetched twice. robots.txt is read once per run and every URL is checked against it.

A manifest may list thousands of works (``scripts/build_legislation_gov_uk_manifest.py``). One work
that is missing (404) or keeps failing with a server error is recorded in ``fetch_errors`` and the run
continues; a refusal (401/403/451, or 429 that persists after back-off) stops the run.
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
from ulis.sources.connectors.moj_laws import robots_allows

# ``ukpga/1998/42``; Acts before 1963 are identified by regnal year (``ukpga/Eliz2/10-11/58``,
# ``ukpga/Vict/22-23/12``), and calendar year + chapter number alone is ambiguous for some of them.
_PATH = re.compile(r"^[a-z]+/(?:\d{4}(?:/[A-Za-z][A-Za-z0-9]*)?|[A-Za-z0-9&]+/\d+(?:-\d+)*)/\d+$")
_DATE = re.compile(rb'<FRBRExpression>.*?<FRBRdate date="(\d{4}-\d{2}-\d{2})', re.S)
MAX_FAILURES_IN_A_ROW = 5
_EVENT = re.compile(rb"<eventRef\b[^>]*>")
_EVENT_DATE = re.compile(rb'\bdate="(\d{4}-\d{2}-\d{2})')
# ``uksi/2018/480@made``: a work that exists in one version only (as enacted / as made).
_COMPACT = re.compile(r"^([^@]+)(?:@(enacted|made))?$")
SINGLE_VERSIONS = ("enacted", "made")


class FetchError(RuntimeError):
    pass


class Refused(FetchError):
    """The publisher refused (401/403/451, or 429 that persists): the run stops, nothing is worked around."""


class OfflineMiss(FetchError):
    """Offline mode and the document is not in the cache."""


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
        # One line per work that could not be fetched (missing, or repeated server errors).
        self.fetch_errors: list[str] = []
        self._robots_txt: str | None = None

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required (fair-use policy)")
        seen: set[str] = set()
        default_history = bool(manifest.get("history_default", False))
        works: list[dict[str, Any]] = []
        for entry in manifest["works"]:
            work = self._work(entry, default_history)
            if not _PATH.match(work["path"]):
                raise ManifestError(f"invalid work path {work['path']!r}")
            if work["path"] in seen:
                raise ManifestError(f"work {work['path']!r} is listed twice")
            seen.add(work["path"])
            works.append(work)
        manifest["works"] = works
        return manifest

    @staticmethod
    def _work(entry: Any, default_history: bool) -> dict[str, Any]:
        """A manifest entry as a dict. A plain string is ``path`` (history per ``history_default``) or ``path@made`` /
        ``path@enacted`` (one version only, no history); a dict may carry ``history``, ``version`` and ``title``."""
        if isinstance(entry, str):
            m = _COMPACT.match(entry)
            if not m:
                raise ManifestError(f"invalid work entry {entry!r}")
            path, version = m.groups()
            return {"path": path, "history": default_history and version is None, "version": version}
        if not isinstance(entry, dict) or "path" not in entry:
            raise ManifestError(f"invalid work entry {entry!r}")
        work = dict(entry)
        work.setdefault("history", default_history)
        if work.get("version") not in (None, *SINGLE_VERSIONS):
            raise ManifestError(f"invalid version {work['version']!r} for {work['path']!r}")
        return work

    def _robots(self, manifest: dict[str, Any], base: str) -> str | None:
        """robots.txt of the site (fetched once per run, cached); None when offline and not cached."""
        if self._robots_txt is None:
            cache_file = self.cache_dir / "robots.txt"
            if cache_file.exists():
                self._robots_txt = cache_file.read_text(encoding="utf-8", errors="replace")
            elif not self.offline:
                got = self._get(manifest, f"{base}/robots.txt", cache_file, check_robots=False)
                self._robots_txt = got[0].decode("utf-8", errors="replace") if got else ""
        return self._robots_txt

    @staticmethod
    def robots_crawl_delay(robots_txt: str) -> float:
        """The ``Crawl-delay`` of the ``*`` group (0 if none)."""
        group_all = False
        for line in robots_txt.splitlines():
            key, _, value = line.split("#", 1)[0].partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                group_all = value == "*"
            elif key == "crawl-delay" and group_all:
                try:
                    return float(value)
                except ValueError:
                    return 0.0
        return 0.0

    def _get(
        self, manifest: dict[str, Any], url: str, cache_file: Path, *, check_robots: bool = True
    ) -> tuple[bytes, dict[str, Any]] | None:
        meta_file = cache_file.with_suffix(".json")
        if cache_file.exists() and meta_file.exists():
            return cache_file.read_bytes(), json.loads(meta_file.read_text())
        if meta_file.exists() and json.loads(meta_file.read_text()).get("status") == 404:
            return None  # a version the site does not have is not asked for again
        if self.offline:
            raise OfflineMiss(f"offline and not cached: {url}")
        if not url.startswith("https://"):
            raise FetchError(f"refusing non-https URL {url}")
        if (
            check_robots
            and self._robots_txt is not None
            and not robots_allows(self._robots_txt, manifest["user_agent"], url)
        ):
            raise Refused(f"{url}: disallowed by robots.txt")
        delay = float(manifest.get("crawl_delay_seconds", 5))
        if self._robots_txt:
            delay = max(delay, self.robots_crawl_delay(self._robots_txt))
        for attempt in range(5):
            wait = self._last_request + delay - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last_request = time.monotonic()
            self.requests_made += 1
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
                    meta_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    meta_file.write_text(json.dumps({"url": url, "status": 404}))
                    return None
                if exc.code in (429, 500, 502, 503, 504) and attempt < 4:
                    retry_after = exc.headers.get("Retry-After")
                    self._sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay * 2**attempt)
                    continue
                if exc.code in (401, 403, 429, 451):
                    raise Refused(f"{url}: HTTP {exc.code} (a refusal is respected, not worked around)") from exc
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
        self._robots(manifest, base)
        failures_in_a_row = 0
        for work in manifest["works"]:
            try:
                yield from self._fetch_work(manifest, base, work)
                failures_in_a_row = 0
            except (Refused, OfflineMiss):
                raise
            except FetchError as exc:
                # One missing or failing work must not abort a run of thousands; a publisher that
                # keeps failing is not hammered.
                self.fetch_errors.append(str(exc))
                failures_in_a_row += 1
                if failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
                    raise FetchError(f"stopping after {failures_in_a_row} failures in a row; last: {exc}") from exc

    def _fetch_work(self, manifest: dict[str, Any], base: str, work: dict[str, Any]) -> Iterator[RawDocument]:
        path = work["path"]
        version = work.get("version")
        if version:  # exists in one version only: ``/enacted`` (Acts) or ``/made`` (instruments)
            got = self._get(manifest, f"{base}/{path}/{version}/data.akn", self.cache_dir / path / f"{version}.akn")
            if got is None:
                raise FetchError(f"{path}: {version} version not found (404)")
            match = _DATE.search(got[0])
            yield self._raw(path, got[0], got[1], match.group(1).decode() if match else version)
            return
        current = self._get(manifest, f"{base}/{path}/data.akn", self.cache_dir / path / "current.akn")
        if current is None:
            raise FetchError(f"{path}: not found (404)")
        data, meta = current
        match = _DATE.search(data)
        current_date = match.group(1).decode() if match else None
        if work.get("history", False) and current_date:
            seen = {current_date}
            # ``history_dates`` restricts the versions fetched (used for small test fixtures). A
            # partial history can hide intermediate versions, so production manifests omit it.
            wanted = work.get("history_dates")
            # Every lifecycle date is a version, except the date an instrument was made (``#made``): the site has no
            # point-in-time version for it (the first one is the date it came into force).
            dates = set()
            for tag in _EVENT.findall(data):
                found = _EVENT_DATE.search(tag)
                if found and b'refersTo="#made"' not in tag:
                    dates.add(found.group(1).decode())
            for d in sorted(dates):
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
