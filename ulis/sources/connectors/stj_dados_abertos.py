"""Fetches the full text of decisions and acórdãos of the Superior Tribunal de Justiça from its open data portal.

Dataset "Íntegras de Decisões Terminativas e Acórdãos do Diário da Justiça" on ``dadosabertos.web.stj.jus.br``
(licence shown on the dataset page: Creative Commons Atribuição). The portal publishes one ZIP of texts and one JSON
file of metadata per day of publication from 2021-01-04 (and one per month for February-April and July 2022). The
manifest (``config/manifests/br-stj-dados-abertos.yaml``) lists that file set - it was read from the dataset page
(``scripts/build_stj_manifest.py``) - and gives each file's resource id and download name.

Access rules (see ``config/sources/br-stj-dados-abertos.yaml``): ``robots.txt`` of the host disallows ``/api/``,
``/revision/``, ``/dataset/rate/`` and ``/dataset/*/history`` and sets ``Crawl-Delay: 10``. ULIS uses **only** the
resource download links (``/dataset/<id>/resource/<id>/download/<name>``) and the dataset page when the manifest is
rebuilt, never the CKAN API; every URL is checked against the rules, requests are sequential and at least 10 s apart
(the larger of the manifest's and robots.txt's delay), HTTPS to this one host only, identifying User-Agent, and every
file is cached under ``<cache_dir>/dadosabertos.web.stj.jus.br/`` so an interrupted run resumes without downloading
again (the cache needs about 10 GB for the whole set). A refusal or network failure stops the run; a file the portal
no longer serves (404) is reported in ``fetch_errors`` and the run continues.

For each file the connector reads the ZIP, takes the metadata from the JSON that some ZIPs contain (2022-2023) or else
from the separate JSON file, and yields **one RawDocument per decision that has both a text file and a metadata
record**: the envelope of :mod:`ulis.sources.parsers.stj_integras` (verbatim metadata records and verbatim text). The
metadata of a RawDocument names the ZIP and JSON URLs and their SHA-256. Metadata records without a text file (the
portal's files from 2026 hold only a few hundred texts) and text files without a record are counted in ``stats`` and
logged, not yielded.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import unicodedata
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.parsers.stj_integras import encode_envelope, record_seq

HOST = "dadosabertos.web.stj.jus.br"
BASE_URL = f"https://{HOST}"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
DATASET_PAGE = f"{BASE_URL}/dataset/integras-de-decisoes-terminativas-e-acordaos-do-diario-da-justica"
MIN_DELAY = 10.0
MAX_MEMBER_BYTES = 50_000_000  # no decision text is anywhere near this; a larger member is not read
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
FILE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")  # includes the portal's literal "___" download name
FILE_ID = re.compile(r"^(?:[0-9]{8}|[0-9]{6})(?:-[b-z])?$")  # day, month; "-b" marks a second upload of the same file
log = logging.getLogger(__name__)

__all__ = [
    "FetchError",
    "Resource",
    "StjConnector",
    "build_file_set",
    "download_url",
    "parse_resources",
    "robots_crawl_delay",
]


def download_url(dataset_id: str, ref: str) -> str:
    """``<resource id>/<download name>`` -> the resource's download link."""
    rid, _, name = ref.partition("/")
    return f"{BASE_URL}/dataset/{dataset_id}/resource/{rid}/download/{name}"


def robots_crawl_delay(robots_txt: str, user_agent: str) -> float | None:
    """The ``Crawl-Delay`` of the group that applies to ``user_agent`` (its product token, else ``*``)."""
    token = user_agent.split("/")[0].strip().lower()
    groups: list[tuple[list[str], float | None]] = []
    agents: list[str] = []
    delay: float | None = None
    seen_rule = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if seen_rule:
                groups.append((agents, delay))
                agents, delay, seen_rule = [], None, False
            agents.append(value.lower())
        elif key in ("allow", "disallow", "crawl-delay") and agents:
            seen_rule = True
            if key == "crawl-delay":
                try:
                    delay = float(value)
                except ValueError:
                    delay = None
    if agents:
        groups.append((agents, delay))
    chosen = [d for a, d in groups if token in a] or [d for a, d in groups if "*" in a]
    values = [d for d in chosen if d is not None]
    return max(values) if values else None


# --------------------------------------------------------------------------- the dataset page -> the file set


@dataclass(frozen=True)
class Resource:
    rid: str
    title: str
    name: str  # last segment of the download link
    kind: str | None  # "json" (metadata), "zip" (texts) or None (the data dictionary, anything else)
    period: str | None  # YYYYMMDD or YYYYMM

    @property
    def ref(self) -> str:
        return f"{self.rid}/{self.name}"


_ITEM = re.compile(r'<li class="resource-item".*?</li>\s*(?=<li class="resource-item"|</ul>)', re.DOTALL)
_ID = re.compile(r'data-id="([0-9a-f-]{36})"')
_TITLE = re.compile(r'class="heading"[^>]*title="([^"]*)"')
_DOWNLOAD = re.compile(r'href="[^"]*/resource/([0-9a-f-]{36})/download/([^"/]+)"')
_PERIOD = re.compile(r"(?<![0-9])([0-9]{8}|[0-9]{6})(?![0-9])")


def parse_resources(page_html: str) -> list[Resource]:
    """The resources of the dataset page, in page order. The portal's titles and download names are inconsistent
    (``metadados20210104``, ``20210104.zip`` that downloads as ``textos20210104.zip``, ``Metadados 20240203``, a
    download name of ``___``), so the kind and period come from the title and the name together."""
    import html as htmllib

    out: list[Resource] = []
    for item in _ITEM.findall(page_html):
        rid_m, title_m, dl_m = _ID.search(item), _TITLE.search(item), _DOWNLOAD.search(item)
        if not (rid_m and title_m and dl_m) or rid_m.group(1) != dl_m.group(1):
            continue
        title, name = htmllib.unescape(title_m.group(1)), htmllib.unescape(dl_m.group(2))
        text = f"{name} {title}".lower()
        kind: str | None
        if ".csv" in text:
            kind = None
        elif "metadados" in text or ".json" in text:
            kind = "json"
        elif ".zip" in text or "textos" in text:
            kind = "zip"
        else:
            kind = None
        period_m = _PERIOD.search(title) or _PERIOD.search(name)
        out.append(Resource(rid_m.group(1), title, name, kind, period_m.group(1) if period_m else None))
    return out


def build_file_set(resources: list[Resource]) -> tuple[list[dict[str, str | None]], list[str]]:
    """Manifest entries ``{id, zip, json}`` (``<resource id>/<download name>``), oldest first, and the periods that
    have metadata but no ZIP of texts. A period listed twice (the portal re-uploaded some days) becomes two entries,
    ``YYYYMMDD`` and ``YYYYMMDD-b``, pairing the first ZIP with the first JSON and so on."""
    by_period: dict[str, dict[str, list[Resource]]] = {}
    for res in resources:
        if res.kind and res.period:
            by_period.setdefault(res.period, {"zip": [], "json": []})[res.kind].append(res)
    files: list[dict[str, str | None]] = []
    without_texts: list[str] = []
    for period in sorted(by_period):
        zips, jsons = by_period[period]["zip"], by_period[period]["json"]
        if not zips:
            without_texts.append(period)
            continue
        for n, z in enumerate(zips):
            suffix = "" if n == 0 else f"-{chr(ord('a') + n)}"
            j = jsons[n] if n < len(jsons) else (jsons[0] if jsons else None)
            files.append({"id": period + suffix, "zip": z.ref, "json": j.ref if j else None})
    return files, without_texts


# --------------------------------------------------------------------------- the connector


def _kind(value: Any) -> str | None:
    """``acordao`` or ``decisao`` for the published ``tipoDocumento``; None when neither (the parser reports it)."""
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().strip().upper()
    return {"ACORDAO": "acordao", "DECISAO": "decisao"}.get(text)


class StjConnector:
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
        self.http: PoliteHttp | None = None
        self.robots: str | None = None
        # one missing file among thousands does not abort the run; a refusal or network failure still does
        self.fetch_errors: list[str] = []
        self.wanted_types: set[str] | None = None  # manifest `document_types`; None = both
        self.scope_skipped = {"files_before_from_period": 0, "documents_of_other_types": 0}
        self.stats = {
            "files": 0,
            "records": 0,
            "documents": 0,
            "records_without_text": 0,
            "texts_without_record": 0,
        }

    # ------------------------------------------------------------------ manifest
    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if str(manifest.get("base_url", "")).rstrip("/") != BASE_URL:
            raise ManifestError(f"base_url must be {BASE_URL}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        if float(manifest.get("crawl_delay_seconds", MIN_DELAY)) < MIN_DELAY:
            raise ManifestError(f"crawl_delay_seconds must be at least {MIN_DELAY:g} (robots.txt Crawl-Delay)")
        if not UUID.match(str(manifest.get("dataset_id", ""))):
            raise ManifestError("dataset_id must be the dataset's UUID")
        files = manifest.get("files") or []
        if not files:
            raise ManifestError("the manifest lists no files")
        from_period = manifest.get("from_period")
        if from_period is not None and not re.fullmatch(r"[0-9]{8}", str(from_period)):
            raise ManifestError("from_period must be a date written YYYYMMDD")
        types = manifest.get("document_types")
        if types is not None and (not isinstance(types, list) or not types or not set(types) <= {"acordao", "decisao"}):
            raise ManifestError("document_types must be a non-empty list of 'acordao' and/or 'decisao'")
        seen: set[str] = set()
        for entry in files:
            file_id = str(entry.get("id", ""))
            if not FILE_ID.match(file_id):
                raise ManifestError(f"invalid file id {file_id!r}")
            if file_id in seen:
                raise ManifestError(f"{file_id} is listed twice")
            seen.add(file_id)
            for key in ("zip", "json"):
                ref = entry.get(key)
                if ref is None and key == "json":
                    continue
                rid, _, name = str(ref or "").partition("/")
                if not (UUID.match(rid) and FILE_NAME.match(name) and name not in (".", "..")):
                    raise ManifestError(f"{file_id}: invalid {key} reference {ref!r}")
        return manifest

    # ------------------------------------------------------------------ http
    def _open(self, manifest: dict[str, Any]) -> PoliteHttp:
        http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=max(MIN_DELAY, float(manifest.get("crawl_delay_seconds", MIN_DELAY))),
            offline=self.offline,
            sleep=self._sleep,
        )
        # No robots.txt (404) would mean no restrictions (RFC 9309); any other failure stops the run.
        got = http.get(ROBOTS_URL, cache_key="robots.txt")
        self.robots = got[0].decode("utf-8", "replace") if got is not None else None
        if self.robots is not None:
            published = robots_crawl_delay(self.robots, http.user_agent)
            if published is not None:
                http.crawl_delay = max(http.crawl_delay, published)
        self.http = http
        return http

    def _get(self, url: str, cache_key: str) -> tuple[bytes, dict[str, Any]] | None:
        assert self.http is not None
        if self.robots is not None and not robots_allows(self.robots, self.http.user_agent, url):
            raise FetchError(f"{url}: disallowed by {ROBOTS_URL}; not fetched")
        return self.http.get(url, cache_key=cache_key)

    # ------------------------------------------------------------------ fetch
    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self._open(manifest)
        dataset_id = manifest["dataset_id"]
        # Scope settings: the file list is the portal's whole set; these narrow what a run downloads and stores.
        # (The whole set is about 2.5 million decisions and 90 GB of database: widen on purpose, not by accident.)
        from_period = str(manifest["from_period"]) if manifest.get("from_period") else None
        self.wanted_types = set(manifest["document_types"]) if manifest.get("document_types") else None
        for entry in manifest["files"]:
            if from_period and str(entry["id"])[:8].ljust(8, "0") < from_period:
                self.scope_skipped["files_before_from_period"] += 1
                continue
            yield from self._file(dataset_id, entry)
        log.info("STJ open data: %s; left out by the manifest scope: %s", self.stats, self.scope_skipped)

    def _file(self, dataset_id: str, entry: dict[str, Any]) -> Iterator[RawDocument]:
        file_id = entry["id"]
        zip_url = download_url(dataset_id, entry["zip"])
        got = self._get(zip_url, f"texts/{file_id}.zip")
        if got is None:
            self.fetch_errors.append(f"stj:{file_id}: {zip_url}: not found (404); skipped")
            return
        data, zip_meta = got
        if not data.startswith(b"PK\x03\x04"):
            self.fetch_errors.append(
                f"stj:{file_id}: {zip_url}: not a ZIP archive ({zip_meta.get('content_type')}); not ingested. "
                f"Remove texts/{file_id}.zip from the cache after checking the portal."
            )
            return
        zip_sha = hashlib.sha256(data).hexdigest()
        retrieved = datetime.fromisoformat(zip_meta["retrieved_at"])
        assert self.http is not None
        path = self.http.cache_path(zip_url, f"texts/{file_id}.zip")
        del data  # a monthly ZIP is 170 MB: read the members from the cached file, not from memory
        try:
            archive = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            self.fetch_errors.append(
                f"stj:{file_id}: {zip_url}: bad ZIP archive ({exc}); not ingested. "
                f"Remove texts/{file_id}.zip from the cache to download it again."
            )
            return
        with archive:
            yield from self._documents(dataset_id, entry, archive, zip_url, zip_meta, zip_sha, retrieved)

    def _documents(
        self,
        dataset_id: str,
        entry: dict[str, Any],
        archive: zipfile.ZipFile,
        zip_url: str,
        zip_meta: dict[str, Any],
        zip_sha: str,
        retrieved: datetime,
    ) -> Iterator[RawDocument]:
        file_id = entry["id"]
        members = {
            Path(info.filename).name[:-4]: info
            for info in archive.infolist()
            if not info.is_dir() and info.filename.lower().endswith(".txt") and info.file_size <= MAX_MEMBER_BYTES
        }
        loaded = self._metadata(dataset_id, entry, archive)
        if loaded is None:
            return
        records, json_source, json_sha = loaded
        self.stats["files"] += 1
        self.stats["records"] += len(records)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            seq = record_seq(record)
            if seq is None:
                self.fetch_errors.append(f"stj:{file_id}: a metadata record has no SeqDocumento; skipped")
                continue
            grouped.setdefault(seq, []).append(record)
        self.stats["texts_without_record"] += len(set(members) - set(grouped))
        for seq, recs in grouped.items():
            info = members.get(seq)
            if info is None:
                self.stats["records_without_text"] += 1
                continue
            try:
                text = archive.read(info).decode("utf-8")
            except (UnicodeDecodeError, zipfile.BadZipFile, RuntimeError) as exc:
                self.fetch_errors.append(f"stj:{file_id}:{seq}: the text file cannot be read ({exc}); skipped")
                continue
            if self.wanted_types is not None and _kind(recs[0].get("tipoDocumento")) not in self.wanted_types:
                self.scope_skipped["documents_of_other_types"] += 1
                continue
            self.stats["documents"] += 1
            yield RawDocument(
                external_id=f"stj:{seq}",
                uri=f"{zip_url}#{info.filename}",
                media_type="application/json",
                content=encode_envelope(seq, recs, text),
                retrieved_at=retrieved,
                metadata={
                    "file": file_id,
                    "tipo": recs[0].get("tipoDocumento"),
                    "member": info.filename,
                    "texts_url": zip_url,
                    "texts_sha256": zip_sha,
                    "metadata_source": json_source,
                    "metadata_sha256": json_sha,
                    "cached_fetch": zip_meta,
                },
            )

    def _metadata(
        self, dataset_id: str, entry: dict[str, Any], archive: zipfile.ZipFile
    ) -> tuple[list[dict[str, Any]], str, str] | None:
        file_id = entry["id"]
        embedded = [i for i in archive.infolist() if not i.is_dir() and i.filename.lower().endswith(".json")]
        if len(embedded) == 1 and embedded[0].file_size <= MAX_MEMBER_BYTES:
            raw, source = archive.read(embedded[0]), f"embedded:{embedded[0].filename}"
        elif entry.get("json"):
            url = download_url(dataset_id, entry["json"])
            got = self._get(url, f"metadata/{file_id}.json")
            if got is None:
                self.fetch_errors.append(f"stj:{file_id}: {url}: not found (404); the day's texts are skipped")
                return None
            raw, source = got[0], url
        else:
            self.fetch_errors.append(f"stj:{file_id}: no metadata file; the day's texts are skipped")
            return None
        try:
            records = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as exc:
            self.fetch_errors.append(f"stj:{file_id}: {source}: the metadata is not valid JSON ({exc}); skipped")
            return None
        if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
            self.fetch_errors.append(f"stj:{file_id}: {source}: the metadata is not a list of records; skipped")
            return None
        return records, source, hashlib.sha256(raw).hexdigest()
