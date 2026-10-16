"""CourtListener bulk data (Free Law Project): United States case law from the publisher's quarterly CSV files.

Free Law Project publishes snapshots of its case-law tables as ``COPY TO`` CSV files compressed with bzip2 in a public
S3 bucket (``com-courtlistener-storage``), documented at
https://wiki.free.law/c/courtlistener/help/api/bulk-data/bulk-legal-data ("Our bulk data files are free of known
copyright
restrictions", Public Domain Mark). The files used here (one generation, named by its date, see the manifest
``config/manifests/us-courtlistener-bulk-s3.yaml``):

* ``courts``          - the court table (about 3,400 rows);
* ``opinion-clusters`` - one row per case (about ten million): name, date filed, source, status, syllabus ...;
* ``dockets``         - one row per docket (tens of millions): the court and the docket number;
* ``citations``       - reporter citations of the clusters;
* ``opinions``        - one row per opinion of a cluster (lead opinion, concurrence, dissent ...) with the text in
  several columns (55 GB compressed, about 350 GB of CSV).

The connector never contacts www.courtlistener.com (it refused the ULIS bot) and does not use the REST API (needs a
token): only the bucket's files and nothing else. The bucket publishes no robots.txt (HTTP 404, which means no
restrictions, RFC 9309); its listing and the files are the publisher's documented way to bulk download.

Because the files are far too large to hold in memory, a run does this, one file at a time, streaming through
``bzip2 -dc``
(or Python's bz2) and a CSV reader:

1. download the files into the cache directory (resumable; one connection at a time; size checked against the manifest);
2. read the clusters (cases) that pass the scope's date, status and "not blocked" filters into a SQLite index;
3. read the dockets of those clusters (court and docket number) into the same index;
4. apply the court rules of the scope (``rules`` in the manifest) and keep the ids of the clusters in scope;
5. read the citations of those clusters;
6. stream the opinions, group the rows of one cluster (rows of a cluster are near each other, not always adjacent: a
   bounded buffer is used, and a late row becomes a ``continuation`` document) and yield one JSON bundle per cluster
   holding the verbatim metadata and the verbatim text of the one text column chosen for each opinion.

Clusters CourtListener marks as blocked (``blocked`` true or a ``date_blocked``) are never taken: that is the
publisher's
removal flag. The scope report (``scripts/courtlistener_scope_report.py``) runs steps 2-3 over the whole publication and
counts clusters by court group, court, year and status, which is how the default scope was sized.
"""

from __future__ import annotations

import csv
import hashlib
import http.client
import io
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import IO, Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.parsers.courtlistener_bulk import (
    BUNDLE_SCHEMA,
    OPINION_TYPE_ORDER,
    TEXT_COLUMNS_ALL,
    TEXT_COLUMNS_DEFAULT,
    bundle_bytes,
    court_for,
)

HOST = "com-courtlistener-storage.s3-us-west-2.amazonaws.com"
BASE_URL = f"https://{HOST}"
BULK_PREFIX = "bulk-data/"
MIN_DELAY = 5.0
FILE_KEYS = ("courts", "dockets", "clusters", "citations", "opinions")
GENERATION = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FILE_NAME = re.compile(r"^[a-z-]+-\d{4}-\d{2}-\d{2}\.csv\.bz2$")
CHUNK = 1 << 20
# Buffer for grouping the rows of one cluster: flush the oldest cluster when more than this many bytes of chosen text
# or this many clusters are waiting.
PENDING_BYTES = 96 * 1024 * 1024
PENDING_CLUSTERS = 40_000

__all__ = [
    "BASE_URL",
    "HOST",
    "CourtListenerBulkConnector",
    "CourtListenerFiles",
    "Scope",
    "build_index",
    "count_clusters",
    "csv_rows",
    "load_manifest",
]


# --------------------------------------------------------------------------- manifest and scope


@dataclass(frozen=True)
class Rule:
    name: str
    courts: frozenset[str]
    groups: frozenset[str]
    from_year: int | None
    to_year: int | None
    statuses: frozenset[str] | None
    # A case whose opinions together hold fewer characters of text (mark-up included) is left out: CourtListener files
    # orders such as "Certiorari denied" as cases of the Supreme Court, about 97% of its 499,000 "cases".
    min_chars: int = 0

    def matches(self, court_id: str, group: str, year: int, status: str, default_statuses: frozenset[str]) -> bool:
        if court_id not in self.courts and group not in self.groups:
            return False
        if (self.from_year is not None and year < self.from_year) or (self.to_year is not None and year > self.to_year):
            return False
        return status in (self.statuses if self.statuses is not None else default_statuses)


@dataclass(frozen=True)
class Scope:
    rules: tuple[Rule, ...]
    exclude_courts: frozenset[str]
    statuses: frozenset[str]
    text_columns: tuple[str, ...]
    min_date: date | None  # the earliest date of any rule (a first, cheap filter while reading clusters)
    max_date: date | None
    modelled_only: bool = True
    # The statuses any rule may take (the default ones and those a rule lists itself): the first, cheap filter.
    candidate_statuses: frozenset[str] = frozenset()

    @classmethod
    def everything(cls) -> Scope:
        """No filter at all (the scope report): every cluster whatever its court, year or status."""
        return cls((), frozenset(), frozenset(), TEXT_COLUMNS_DEFAULT, None, None, modelled_only=False)

    def selects(self, court_id: str, group: str, year: int, status: str) -> bool:
        if court_id in self.exclude_courts:
            return False
        return any(r.matches(court_id, group, year, status, self.statuses) for r in self.rules)


def _scope_from(manifest: dict[str, Any]) -> Scope:
    raw = manifest.get("scope") or {}
    rules_raw = raw.get("rules") or []
    if not rules_raw:
        raise ManifestError("scope.rules must list at least one rule")
    rules: list[Rule] = []
    for entry in rules_raw:
        name = str(entry.get("name") or "")
        courts = frozenset(str(c) for c in entry.get("courts") or [])
        groups = frozenset(str(g) for g in entry.get("groups") or [])
        if not name or not (courts or groups):
            raise ManifestError(f"scope rule {entry!r} needs a name and courts or groups")
        statuses = entry.get("statuses")
        rules.append(
            Rule(
                name,
                courts,
                groups,
                _year(entry.get("from_year")),
                _year(entry.get("to_year")),
                frozenset(statuses) if statuses else None,
                _min_chars(entry.get("min_chars")),
            )
        )
    statuses = frozenset(raw.get("statuses") or ["Published"])
    columns = tuple(raw.get("text_columns") or TEXT_COLUMNS_DEFAULT)
    bad = [c for c in columns if c not in TEXT_COLUMNS_ALL]
    if bad or len(set(columns)) != len(columns):
        raise ManifestError(f"scope.text_columns must be distinct columns among {TEXT_COLUMNS_ALL}: {columns}")
    firsts = [r.from_year for r in rules]
    lasts = [r.to_year for r in rules]
    min_year = None if any(y is None for y in firsts) else min(y for y in firsts if y is not None)
    max_year = None if any(y is None for y in lasts) else max(y for y in lasts if y is not None)
    return Scope(
        tuple(rules),
        frozenset(str(c) for c in raw.get("exclude_courts") or []),
        statuses,
        columns,
        date(min_year, 1, 1) if min_year else None,
        date(max_year, 12, 31) if max_year else None,
        candidate_statuses=statuses.union(*(r.statuses or frozenset() for r in rules)),
    )


def _min_chars(value: Any) -> int:
    if value is None:
        return 0
    if not isinstance(value, int) or not 0 <= value <= 10_000_000:
        raise ManifestError(f"min_chars must be a non-negative integer, got {value!r}")
    return value


def _year(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or not 1000 <= value <= 2200:
        raise ManifestError(f"a year must be an integer between 1000 and 2200, got {value!r}")
    return value


def load_manifest(path: Path, expected_source_id: str) -> dict[str, Any]:
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("source_id") != expected_source_id
    ):
        raise ManifestError("manifest schema or source_id mismatch")
    if str(manifest.get("base_url", "")).rstrip("/") != BASE_URL:
        raise ManifestError(f"base_url must be {BASE_URL}")
    if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
        raise ManifestError("an identifying user_agent with contact details is required")
    if float(manifest.get("crawl_delay_seconds", MIN_DELAY)) < MIN_DELAY:
        raise ManifestError(f"crawl_delay_seconds must be at least {MIN_DELAY:g}")
    generation = str(manifest.get("generation", ""))
    if not GENERATION.match(generation):
        raise ManifestError("generation must be a YYYY-MM-DD date")
    files = manifest.get("files") or {}
    if set(files) != set(FILE_KEYS):
        raise ManifestError(f"files must list exactly {FILE_KEYS}")
    for key, spec in files.items():
        name = str(spec.get("name", ""))
        if (
            not FILE_NAME.match(name)
            or generation not in name
            or not isinstance(spec.get("bytes"), int)
            or spec["bytes"] < 1
        ):
            raise ManifestError(f"files.{key}: name must be <kind>-{generation}.csv.bz2 and bytes a positive integer")
    limit = manifest.get("limit")
    if limit is not None and (not isinstance(limit, int) or limit < 1):
        raise ManifestError("limit must be a positive integer")
    scope = _scope_from(manifest)
    only = manifest.get("only_rules")
    if only is not None:
        known = {r.name for r in scope.rules}
        if not isinstance(only, list) or not only or not set(only) <= known:
            raise ManifestError(f"only_rules must be a non-empty list of rule names among {sorted(known)}")
    return manifest


# --------------------------------------------------------------------------- files


class CourtListenerFiles:
    """Where the bulk files of a generation are, downloading what is missing (one file at a time, resumable)."""

    def __init__(
        self,
        manifest: dict[str, Any],
        cache_dir: Path,
        *,
        local_dir: Path | None = None,
        offline: bool = False,
        sleep: Any = time.sleep,
    ) -> None:
        self.manifest = manifest
        self.local_dir = local_dir
        self.offline = offline
        self._sleep = sleep
        self.user_agent = manifest["user_agent"]
        self.delay = float(manifest.get("crawl_delay_seconds", MIN_DELAY))
        self.dir = cache_dir / HOST / BULK_PREFIX.rstrip("/")
        self.downloads: list[str] = []

    def spec(self, key: str) -> dict[str, Any]:
        return self.manifest["files"][key]  # type: ignore[no-any-return]

    def url(self, key: str) -> str:
        return f"{BASE_URL}/{BULK_PREFIX}{self.spec(key)['name']}"

    def path(self, key: str) -> Path:
        name = self.spec(key)["name"]
        if self.local_dir is not None:
            # A slice may be given as the uncompressed CSV or the bz2 file itself.
            for candidate in (self.local_dir / name, self.local_dir / name.removesuffix(".bz2")):
                if candidate.exists():
                    return candidate
            raise FetchError(f"{name} is not in {self.local_dir}")
        return self.dir / name

    def ensure(self, key: str) -> Path:
        path = self.path(key)
        if self.local_dir is not None:
            return path
        size = self.spec(key)["bytes"]
        if path.exists() and path.stat().st_size == size:
            return path
        if self.offline:
            raise FetchError(f"offline and not downloaded: {self.url(key)}")
        self._download(self.url(key), path, size)
        return path

    def ensure_all(self) -> None:
        for key in FILE_KEYS:
            self.ensure(key)

    # ---- download
    def _open(self, url: str, start: int, method: str = "GET") -> Any:
        headers = {"User-Agent": self.user_agent}
        if start:
            headers["Range"] = f"bytes={start}-"
        request = urllib.request.Request(url, headers=headers, method=method)  # noqa: S310 - https only, checked below
        return urllib.request.urlopen(request, timeout=120)  # noqa: S310

    def _download(self, url: str, path: Path, size: int) -> None:
        if not url.startswith(f"{BASE_URL}/"):
            raise FetchError(f"refusing {url}: only {BASE_URL}")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        part = path.with_name(path.name + ".part")
        self._sleep(0)
        for attempt in range(12):
            have = part.stat().st_size if part.exists() else 0
            if have == size:
                break
            if have > size:
                part.unlink()
                have = 0
            self._sleep(self.delay if attempt == 0 else min(300.0, self.delay * 2**attempt))
            try:
                with self._open(url, have) as response:
                    if response.status not in (200, 206) or (have and response.status != 206):
                        raise FetchError(f"{url}: HTTP {response.status} for a range request from byte {have}")
                    total = have + int(response.headers.get("Content-Length") or 0)
                    if total != size:
                        raise FetchError(f"{url}: the server reports {total} bytes, the manifest says {size}")
                    with part.open("ab") as out:
                        while chunk := response.read(CHUNK):
                            out.write(chunk)
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504):
                    continue
                raise FetchError(f"{url}: HTTP {exc.code} (not retried: a refusal is respected)") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
                continue
        if not part.exists() or part.stat().st_size != size:
            raise FetchError(f"{url}: could not download all {size} bytes")
        part.rename(path)
        self.downloads.append(path.name)


# --------------------------------------------------------------------------- CSV reading


def csv_rows(path: Path, columns: tuple[str, ...] | None = None) -> Iterator[dict[str, str]]:
    """Rows of a bulk CSV (PostgreSQL ``COPY ... CSV HEADER ESCAPE '\\'``), streamed.

    ``columns``: only these keys are kept in each dict (saves memory on the huge text columns). A row with the
    wrong number
    of fields is reported by raising ``ValueError`` after yielding everything before it.
    """
    csv.field_size_limit(2**31 - 1)
    proc: subprocess.Popen[bytes] | None = None
    stream: IO[str]
    if path.suffix == ".bz2":
        # lbzip2 decompresses a single bzip2 file on all cores (apt install lbzip2): about 4x faster than bzip2 on
        # 4 cores,
        # which matters for the 55 GB opinions file; plain bzip2 is the fallback, Python's bz2 the last resort.
        tool = next((t for t in ("lbzip2", "bzip2") if shutil.which(t)), None)
        if tool:
            proc = subprocess.Popen([tool, "-dc", str(path)], stdout=subprocess.PIPE, bufsize=CHUNK)  # noqa: S603
            assert proc.stdout is not None
            stream = io.TextIOWrapper(proc.stdout, encoding="utf-8", newline="")
        else:  # pragma: no cover - bzip2 is installed everywhere the server image is built
            import bz2

            stream = bz2.open(path, "rt", encoding="utf-8", newline="")  # noqa: SIM115 - closed in the finally below
    else:
        stream = path.open(encoding="utf-8", newline="")
    try:
        reader = csv.reader(stream, escapechar="\\")
        header = next(reader)
        keep = [(i, name) for i, name in enumerate(header) if columns is None or name in columns]
        width = len(header)
        for row in reader:
            if len(row) != width:
                raise ValueError(f"{path.name}: a row has {len(row)} fields, the header {width}")
            yield {name: row[i] for i, name in keep}
    finally:
        stream.close()
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


def read_courts(path: Path) -> dict[str, dict[str, str]]:
    return {row["id"]: row for row in csv_rows(path)}


# --------------------------------------------------------------------------- index (SQLite)

CLUSTER_COLUMNS = (
    "id",
    "docket_id",
    "date_filed",
    "date_filed_is_approximate",
    "case_name",
    "case_name_full",
    "source",
    "precedential_status",
    "blocked",
    "date_blocked",
    "judges",
    "syllabus",
    "disposition",
    "scdb_id",
    "filepath_json_harvard",
)
DOCKET_COLUMNS = ("id", "court_id", "docket_number", "case_name", "blocked", "date_blocked")
CITATION_COLUMNS = ("cluster_id", "volume", "reporter", "page", "type")
OPINION_META = (
    "id",
    "type",
    "author_str",
    "joined_by_str",
    "per_curiam",
    "sha1",
    "download_url",
    "extracted_by_ocr",
    "cluster_id",
)


def _is_true(value: str) -> bool:
    return value.lower() in ("t", "true", "1")


@dataclass
class IndexStats:
    clusters_read: int = 0
    clusters_kept: int = 0
    clusters_blocked: int = 0
    clusters_undated: int = 0
    dockets_read: int = 0
    dockets_kept: int = 0
    clusters_in_scope: int = 0
    clusters_court_not_modelled: int = 0
    clusters_without_docket: int = 0
    citations_kept: int = 0
    by_rule: dict[str, int] = field(default_factory=dict)


def build_index(
    files: CourtListenerFiles,
    scope: Scope,
    index_path: Path,
    *,
    keep_text: bool = True,
    progress: Any = None,
) -> IndexStats:
    """Steps 2-5 of the module docstring. Writes ``index_path`` (SQLite) and returns counts.

    ``keep_text`` False keeps only what a count needs (the scope report). The index is built once: a finished index
    carries a ``done`` row and is reused by later runs with the same scope.
    """
    say = progress or (lambda message: None)
    stats = IndexStats()
    tmp = index_path.with_name(index_path.name + ".building")
    if tmp.exists():
        tmp.unlink()
    index_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(
        """
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=FILE;
        CREATE TABLE clusters (id INTEGER PRIMARY KEY, docket_id INTEGER, year INTEGER, date_filed TEXT, approx INTEGER,
          status TEXT, source TEXT, name TEXT, name_full TEXT, judges TEXT, syllabus TEXT, disposition TEXT,
          scdb_id TEXT, harvard_path TEXT);
        CREATE TABLE dockets (id INTEGER PRIMARY KEY, court_id TEXT, number TEXT, name TEXT, blocked INTEGER);
        CREATE TABLE scope (id INTEGER PRIMARY KEY, court_id TEXT, rule TEXT);
        CREATE TABLE citations (cluster_id INTEGER, volume TEXT, reporter TEXT, page TEXT, type INTEGER);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    # ---- courts
    courts = read_courts(files.ensure("courts"))
    groups = {cid: row["jurisdiction"] for cid, row in courts.items()}

    # ---- clusters
    cluster_columns = CLUSTER_COLUMNS if keep_text else tuple(c for c in CLUSTER_COLUMNS if c not in ("syllabus",))
    needed_dockets = bytearray(1)
    batch: list[tuple[Any, ...]] = []
    for row in csv_rows(files.ensure("clusters"), cluster_columns):
        stats.clusters_read += 1
        if stats.clusters_read % 1_000_000 == 0:
            say(f"clusters read: {stats.clusters_read:,}")
        filed = row["date_filed"]
        if not filed:
            stats.clusters_undated += 1
            continue
        year = int(filed[:4])
        if scope.min_date and filed < scope.min_date.isoformat():
            continue
        if scope.max_date and filed > scope.max_date.isoformat():
            continue
        if scope.candidate_statuses and row["precedential_status"] not in scope.candidate_statuses:
            continue
        if _is_true(row["blocked"]) or row["date_blocked"]:
            stats.clusters_blocked += 1
            continue
        docket_id = int(row["docket_id"])
        if docket_id >= len(needed_dockets) * 8:
            needed_dockets.extend(bytearray(max(1, (docket_id * 8 // 7 - len(needed_dockets) * 8) // 8 + 1024)))
        needed_dockets[docket_id >> 3] |= 1 << (docket_id & 7)
        batch.append(
            (
                int(row["id"]),
                docket_id,
                year,
                filed,
                int(_is_true(row["date_filed_is_approximate"])),
                row["precedential_status"],
                row["source"],
                row["case_name"] if keep_text else None,
                row["case_name_full"] if keep_text else None,
                row["judges"] if keep_text else None,
                row.get("syllabus") if keep_text else None,
                row["disposition"] if keep_text else None,
                row["scdb_id"] if keep_text else None,
                row["filepath_json_harvard"] if keep_text else None,
            )
        )
        stats.clusters_kept += 1
        if len(batch) >= 50_000:
            db.executemany("INSERT INTO clusters VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
            batch.clear()
    db.executemany("INSERT INTO clusters VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
    batch.clear()
    db.commit()
    say(f"clusters kept: {stats.clusters_kept:,} of {stats.clusters_read:,}")

    # ---- dockets (only those of the kept clusters)
    for row in csv_rows(files.ensure("dockets"), DOCKET_COLUMNS):
        stats.dockets_read += 1
        if stats.dockets_read % 5_000_000 == 0:
            say(f"dockets read: {stats.dockets_read:,}")
        did = int(row["id"])
        if (did >> 3) >= len(needed_dockets) or not needed_dockets[did >> 3] & (1 << (did & 7)):
            continue
        blocked = int(_is_true(row["blocked"]) or bool(row["date_blocked"]))
        batch.append((did, row["court_id"], row["docket_number"], row["case_name"] if keep_text else None, blocked))
        stats.dockets_kept += 1
        if len(batch) >= 50_000:
            db.executemany("INSERT OR IGNORE INTO dockets VALUES (?,?,?,?,?)", batch)
            batch.clear()
    db.executemany("INSERT OR IGNORE INTO dockets VALUES (?,?,?,?,?)", batch)
    batch.clear()
    db.commit()
    del needed_dockets
    say(f"dockets kept: {stats.dockets_kept:,} of {stats.dockets_read:,}")

    # ---- scope: court rules
    cursor = db.execute(
        "SELECT c.id, c.year, c.status, d.court_id, d.blocked FROM clusters c LEFT JOIN dockets d ON d.id = c.docket_id"
    )
    for cluster_id, year, status, court_id, docket_blocked in cursor:
        if court_id is None:
            stats.clusters_without_docket += 1
            continue
        if docket_blocked:  # the publisher's removal flag on the docket applies to its clusters
            stats.clusters_blocked += 1
            continue
        group = groups.get(court_id, "")
        if scope.modelled_only and court_for(court_id) is None:
            stats.clusters_court_not_modelled += 1
            continue
        rule = _first_rule(scope, court_id, group, year, status)
        if rule is None and scope.rules:
            continue
        label = rule or "all"
        batch.append((cluster_id, court_id, label))
        stats.by_rule[label] = stats.by_rule.get(label, 0) + 1
        stats.clusters_in_scope += 1
        if len(batch) >= 50_000:
            db.executemany("INSERT INTO scope VALUES (?,?,?)", batch)
            batch.clear()
    db.executemany("INSERT INTO scope VALUES (?,?,?)", batch)
    batch.clear()
    db.commit()
    say(f"clusters in scope: {stats.clusters_in_scope:,}")

    # ---- citations of the clusters in scope
    if keep_text:
        in_scope = _bitmap(row[0] for row in db.execute("SELECT id FROM scope"))
        for row in csv_rows(files.ensure("citations"), CITATION_COLUMNS):
            cid = int(row["cluster_id"])
            if (cid >> 3) >= len(in_scope) or not in_scope[cid >> 3] & (1 << (cid & 7)):
                continue
            batch.append((cid, row["volume"], row["reporter"], row["page"], int(row["type"] or 0)))
            stats.citations_kept += 1
            if len(batch) >= 50_000:
                db.executemany("INSERT INTO citations VALUES (?,?,?,?,?)", batch)
                batch.clear()
        db.executemany("INSERT INTO citations VALUES (?,?,?,?,?)", batch)
        db.execute("CREATE INDEX citations_cluster ON citations(cluster_id)")
        db.commit()
        say(f"citations kept: {stats.citations_kept:,}")
    db.execute("INSERT INTO meta VALUES ('stats', ?)", (json.dumps(stats.__dict__),))
    db.execute("INSERT INTO meta VALUES ('done', '1')")
    db.commit()
    db.close()
    tmp.rename(index_path)
    return stats


def _first_rule(scope: Scope, court_id: str, group: str, year: int, status: str) -> str | None:
    if not scope.rules:
        return None
    if court_id in scope.exclude_courts:
        return None
    for rule in scope.rules:
        if rule.matches(court_id, group, year, status, scope.statuses):
            return rule.name
    return None


def _bitmap(ids: Iterator[int] | Any) -> bytearray:
    values = sorted(ids)
    bits = bytearray((values[-1] >> 3) + 1 if values else 1)
    for v in values:
        bits[v >> 3] |= 1 << (v & 7)
    return bits


def count_clusters(index_path: Path) -> list[tuple[str, str, int, str, int]]:
    """(court_id, source code, year, status, clusters) for the whole index; used by the scope report."""
    db = sqlite3.connect(index_path)
    try:
        return list(
            db.execute(
                "SELECT d.court_id, c.source, c.year, c.status, count(*) FROM clusters c "
                "JOIN dockets d ON d.id = c.docket_id "
                "WHERE d.blocked = 0 GROUP BY 1, 2, 3, 4"
            )
        )
    finally:
        db.close()


# --------------------------------------------------------------------------- connector


class CourtListenerBulkConnector:
    def __init__(
        self,
        manifest_path: Path,
        expected_source_id: str,
        cache_dir: Path,
        *,
        offline: bool = False,
        sleep: Any = time.sleep,
        local_dir: Path | None = None,
    ) -> None:
        self.manifest_path = manifest_path
        self.expected_source_id = expected_source_id
        self.cache_dir = cache_dir
        self.offline = offline
        self._sleep = sleep
        self.local_dir = local_dir
        self.fetch_errors: list[str] = []
        self.skipped_small = 0  # cases left out by a rule's min_chars (not errors)
        self.index_stats: IndexStats | None = None
        self.progress: Any = lambda message: print(f"courtlistener: {message}", file=sys.stderr)  # noqa: T201

    def _manifest(self) -> dict[str, Any]:
        manifest = load_manifest(self.manifest_path, self.expected_source_id)
        if self.local_dir is None and manifest.get("local_dir"):
            self.local_dir = Path(str(manifest["local_dir"]))
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        scope = _scope_from(manifest)
        generation = manifest["generation"]
        files = CourtListenerFiles(
            manifest, self.cache_dir, local_dir=self.local_dir, offline=self.offline, sleep=self._sleep
        )
        files.ensure_all()
        indexed = {
            **manifest["scope"],
            "rules": [{k: v for k, v in r.items() if k != "min_chars"} for r in manifest["scope"]["rules"]],
        }
        digest = hashlib.sha256(json.dumps(indexed, sort_keys=True, default=str).encode()).hexdigest()[:12]
        index_path = self.cache_dir / HOST / f"index-{generation}-{digest}.sqlite"
        if index_path.exists():
            self.index_stats = IndexStats(**json.loads(_meta(index_path, "stats")))
        else:
            self.index_stats = build_index(files, scope, index_path, progress=self.progress)
        courts = read_courts(files.path("courts"))
        retrieved = datetime.fromtimestamp(files.path("opinions").stat().st_mtime, UTC)
        limit = manifest.get("limit")
        opinions_url = files.url("opinions")
        db = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        only = manifest.get("only_rules")
        if only:
            marks = ",".join("?" for _ in only)
            in_scope = _bitmap(row[0] for row in db.execute(f"SELECT id FROM scope WHERE rule IN ({marks})", only))
        else:
            in_scope = _bitmap(row[0] for row in db.execute("SELECT id FROM scope"))
        emitted = bytearray(len(in_scope))
        min_chars = {r.name: r.min_chars for r in scope.rules if r.min_chars}
        count = 0
        try:
            for cluster_id, rows in self._grouped(files.path("opinions"), in_scope, scope):
                document = self._bundle(
                    db, courts, cluster_id, rows, emitted, generation, opinions_url, retrieved, min_chars
                )
                if document is None:
                    continue
                yield document
                count += 1
                if count % 25_000 == 0:
                    self.progress(f"cases handed to the pipeline: {count:,}")
                if limit is not None and count >= limit:
                    return
        finally:
            db.close()
            if self.skipped_small:
                self.progress(f"cases below their rule's min_chars, left out: {self.skipped_small:,}")

    # ---- grouping
    def _grouped(self, path: Path, in_scope: bytearray, scope: Scope) -> Iterator[tuple[int, list[dict[str, str]]]]:
        columns = (*OPINION_META, *scope.text_columns)
        pending: OrderedDict[int, list[dict[str, str]]] = OrderedDict()
        pending_bytes = 0
        try:
            for row in csv_rows(path, columns):
                cid = int(row["cluster_id"])
                if (cid >> 3) >= len(in_scope) or not in_scope[cid >> 3] & (1 << (cid & 7)):
                    continue
                chosen = next((c for c in scope.text_columns if row[c]), None)
                if chosen is None:
                    continue
                slim = {k: row[k] for k in OPINION_META}
                slim["text_column"] = chosen
                slim["text"] = row[chosen]
                pending.setdefault(cid, []).append(slim)
                pending_bytes += len(slim["text"])
                while pending and (pending_bytes > PENDING_BYTES or len(pending) > PENDING_CLUSTERS):
                    oldest, rows = pending.popitem(last=False)
                    pending_bytes -= sum(len(r["text"]) for r in rows)
                    yield oldest, rows
        except ValueError as exc:
            self.fetch_errors.append(f"opinions: {exc}; the rest of the file was not read")
        for cid, rows in pending.items():
            yield cid, rows

    def _bundle(
        self,
        db: sqlite3.Connection,
        courts: dict[str, dict[str, str]],
        cluster_id: int,
        rows: list[dict[str, str]],
        emitted: bytearray,
        generation: str,
        opinions_url: str,
        retrieved: datetime,
        min_chars: dict[str, int],
    ) -> RawDocument | None:
        cluster = db.execute("SELECT * FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
        docket = (
            db.execute("SELECT * FROM dockets WHERE id = ?", (cluster["docket_id"],)).fetchone() if cluster else None
        )
        if cluster is None or docket is None:
            self.fetch_errors.append(f"cl:cluster:{cluster_id}: no cluster or docket in the index; not ingested")
            return None
        court = courts.get(docket["court_id"])
        if court is None or court_for(docket["court_id"]) is None:
            self.fetch_errors.append(
                f"cl:cluster:{cluster_id}: court {docket['court_id']!r} is not modelled; not ingested"
            )
            return None
        rows = sorted(rows, key=lambda r: (OPINION_TYPE_ORDER.get(r["type"], 99), int(r["id"])))
        continuation = bool(emitted[cluster_id >> 3] & (1 << (cluster_id & 7)))
        if min_chars and not continuation:
            row = db.execute("SELECT rule FROM scope WHERE id = ?", (cluster_id,)).fetchone()
            if row is not None and sum(len(r["text"]) for r in rows) < min_chars.get(row[0], 0):
                self.skipped_small += 1
                return None
        emitted[cluster_id >> 3] |= 1 << (cluster_id & 7)
        citations = [
            {"volume": c["volume"], "reporter": c["reporter"], "page": c["page"], "type": c["type"]}
            for c in db.execute(
                "SELECT volume, reporter, page, type FROM citations WHERE cluster_id = ? ORDER BY rowid", (cluster_id,)
            )
        ]
        bundle: dict[str, Any] = {
            "schema": BUNDLE_SCHEMA,
            "continuation": continuation,
            "court": {
                k: court[k]
                for k in ("id", "full_name", "short_name", "citation_string", "jurisdiction", "start_date", "end_date")
            },
            "docket": {"id": docket["id"], "docket_number": docket["number"], "case_name": docket["name"]},
            "cluster": {
                "id": cluster["id"],
                "case_name": cluster["name"],
                "case_name_full": cluster["name_full"],
                "date_filed": cluster["date_filed"],
                "date_filed_is_approximate": bool(cluster["approx"]),
                "source": cluster["source"],
                "precedential_status": cluster["status"],
                "judges": cluster["judges"],
                "disposition": cluster["disposition"],
                "syllabus": "" if continuation else cluster["syllabus"],
                "scdb_id": cluster["scdb_id"],
                "filepath_json_harvard": cluster["harvard_path"],
            },
            "citations": citations,
            "opinions": [
                {
                    "id": int(r["id"]),
                    "type": r["type"],
                    "author_str": r["author_str"],
                    "joined_by_str": r["joined_by_str"],
                    "per_curiam": _is_true(r["per_curiam"]),
                    "sha1": r["sha1"],
                    "download_url": r["download_url"],
                    "extracted_by_ocr": _is_true(r["extracted_by_ocr"]),
                    "text_column": r["text_column"],
                    "text": r["text"],
                }
                for r in rows
            ],
        }
        suffix = f":{bundle['opinions'][0]['id']}" if continuation else ""
        return RawDocument(
            external_id=f"cl:cluster:{cluster_id}{suffix}",
            uri=f"{opinions_url}#cluster-{cluster_id}",
            media_type="application/json",
            content=bundle_bytes(bundle),
            retrieved_at=retrieved,
            metadata={
                "generation": generation,
                "cluster_id": cluster_id,
                "opinion_ids": [o["id"] for o in bundle["opinions"]],
            },
        )


def _meta(index_path: Path, key: str) -> str:
    db = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    try:
        row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    finally:
        db.close()
    if row is None:
        raise FetchError(f"{index_path} is not a finished index (no {key!r}); delete it to rebuild")
    return str(row[0])
