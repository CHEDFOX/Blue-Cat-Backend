"""Fetches the decisions of the Data Protection Ombudsman from Finlex's open data download package.

Finlex offers every open-data document type as one daily-updated zip archive
(https://www.finlex.fi/en/open-data/download-materials); the Data Protection Ombudsman's decisions are
``https://www.finlex.fi/files/open-data/archives/data-protection-ombudsman-decision.zip`` (3.6 MB, about
400 decisions). Licence CC BY 4.0 (Finlex FAQ); see ``config/sources/fi-finlex-dpo-decisions.yaml``.

One request fetches the archive (``PoliteHttp``: identifying User-Agent, HTTPS to ``www.finlex.fi`` only,
refusals respected, cached under ``<cache_dir>/www.finlex.fi/files/open-data/archives/``); ``robots.txt``
is read first and the archive path is checked against it. The archive is read in memory, so a size limit
(``max_archive_bytes``, default 64 MB) refuses larger packages - the Chancellor of Justice and Parliamentary
Ombudsman packages (227 MB, 567 MB) are not handled by this connector, and their decisions keep their text in
attached PDFs anyway. Each ``akn/fi/judgment/<type>/<year>/<number>/<fin|swe>@/main.xml`` member is
yielded exactly as stored, oldest first, after checking that it is the document of that member's own path.
A member that is not well-formed is skipped and reported in ``fetch_errors``.
"""

from __future__ import annotations

import io
import re
import time
import zipfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from defusedxml.ElementTree import fromstring

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

HOST = "www.finlex.fi"
BASE_URL = f"https://{HOST}"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
MIN_DELAY = 5.0
TYPE = "data-protection-ombudsman-decision"
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
_MEMBER = re.compile(rf"^akn/fi/judgment/{TYPE}/(\d{{4}})/(\d{{1,6}})/(fin|swe)@/main\.xml$")
_ARCHIVE = re.compile(rf"^files/open-data/archives/{TYPE}\.zip$")
AKN = "{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}"

__all__ = ["FetchError", "FinlexDecisionsConnector"]


class FinlexDecisionsConnector:
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
        self.cache_dir = cache_dir
        self.offline = offline
        self._sleep = sleep
        self.fetch_errors: list[str] = []
        self.http: PoliteHttp | None = None

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if str(manifest.get("base_url", "")).rstrip("/") != BASE_URL:
            raise ManifestError(f"base_url must be {BASE_URL}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        if float(manifest.get("crawl_delay_seconds", MIN_DELAY)) < MIN_DELAY:
            raise ManifestError(f"crawl_delay_seconds must be at least {MIN_DELAY:g}")
        if not _ARCHIVE.match(str(manifest.get("archive", ""))):
            raise ManifestError(f"archive must be files/open-data/archives/{TYPE}.zip")
        languages = manifest.get("languages") or []
        if not languages or any(lang not in ("fin", "swe") for lang in languages):
            raise ManifestError("languages must be a non-empty list of fin and swe")
        years, limit = manifest.get("years"), manifest.get("limit")
        if years is not None and not (isinstance(years, list) and all(isinstance(y, int) for y in years)):
            raise ManifestError("years must be a list of years or null")
        if limit is not None and not isinstance(limit, int):
            raise ManifestError("limit must be an integer or null")
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", MIN_DELAY)),
            offline=self.offline,
            sleep=self._sleep,
        )
        got = self.http.get(ROBOTS_URL, cache_key="robots.txt")
        robots = got[0].decode("utf-8", "replace") if got is not None else ""  # 404: no restrictions
        archive_url = f"{BASE_URL}/{manifest['archive']}"
        if not robots_allows(robots, self.http.user_agent, archive_url):
            raise FetchError(f"{archive_url}: disallowed by {ROBOTS_URL}; not fetched")
        got = self.http.get(archive_url)
        if got is None:
            raise FetchError(f"{archive_url}: not found (404)")
        data, meta = got
        if len(data) > int(manifest.get("max_archive_bytes", DEFAULT_MAX_BYTES)):
            raise FetchError(f"{archive_url}: {len(data)} bytes exceeds max_archive_bytes; not read")
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise FetchError(f"{archive_url}: not a zip archive ({exc})") from exc
        years = set(manifest.get("years") or [])
        languages = set(manifest["languages"])
        members: list[tuple[int, int, str, str]] = []
        for name in archive.namelist():
            m = _MEMBER.match(name)
            if m and m.group(3) in languages and (not years or int(m.group(1)) in years):
                members.append((int(m.group(1)), int(m.group(2)), m.group(3), name))
        members.sort()
        limit = manifest.get("limit")
        if limit is not None:
            members = members[: int(limit)]
        retrieved_at = datetime.fromisoformat(meta["retrieved_at"])
        for year, number, language, name in members:
            content = archive.read(name)
            try:
                root = fromstring(content, forbid_dtd=True)
            except Exception as exc:  # defusedxml raises several exception types
                self.fetch_errors.append(f"{name}: not well-formed XML ({exc}); skipped")
                continue
            expected = f"/akn/fi/judgment/{TYPE}/{year}/{number}"
            uri = root.find(f"{AKN}judgment/{AKN}meta/{AKN}identification/{AKN}FRBRWork/{AKN}FRBRuri")
            if uri is None or uri.get("value") != expected:
                self.fetch_errors.append(f"{name}: the document is not {expected}; skipped")
                continue
            yield RawDocument(
                external_id=f"finlex-dpo:{year}/{number}/{language}",
                uri=f"{archive_url}#{name}",
                media_type="application/xml",
                content=content,
                retrieved_at=retrieved_at,
                metadata={"archive": archive_url, "member": name, "cached_fetch": meta},
            )
