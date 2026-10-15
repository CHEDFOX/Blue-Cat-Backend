"""Fetches Estonian consolidated texts (terviktekstid) from Riigi Teataja's open-data archive.

Riigi Teataja (Ministry of Justice and Digital Affairs) publishes its whole corpus of act XML as
yearly zip archives under ``https://www.riigiteataja.ee/avaandmed/ERT/`` ("Riigi Teataja
avaandmed", with ``LOEMIND.txt`` naming the generation date and SHA-256 sums). The archive of the
current year (``xml.<year>.zip``, about 43 GB, 254 000 files named ``<globaalID>.xml``) holds every
version of every act. The site's per-act ``/akt/<id>.xml`` links are served by the web application
only (they return the HTML shell), so the archive is the documented XML access.

ULIS never downloads the archive. The zip format allows reading single members with HTTP range
requests, which the server supports (``Accept-Ranges: bytes``); four kinds of small requests are
made, all through :class:`PoliteHttp` (identifying User-Agent, at least 5 s apart, back-off,
refusals respected, cached under ``var/sources/www.riigiteataja.ee/``):

1. ``/robots.txt`` and ``LOEMIND.txt`` (the generation date of the archive);
2. the last 64 KiB of the archive (end of central directory record, zip64 aware);
3. the central directory (about 25 MB for 2026, once; cached);
4. each member listed in the manifest: its local header and compressed data.

The manifest (``config/manifests/ee-riigi-teataja.yaml``) lists, per work, the ``globaalID`` of
each consolidated text (redaktsioon) to fetch, oldest first (snapshots are recorded oldest first,
see ``ulis.ingestion.snapshots``). Nothing is discovered or inferred. The decompressed XML is
yielded exactly as stored in the archive after its CRC-32 and size are checked against the central
directory.

robots.txt is applied exactly (the shared :class:`PoliteHttp` does not read it): the archive path
and each listed act's page (``/akt/<id>``) are matched against the ``User-agent: *`` rules, with
``*`` wildcards and ``$`` anchors, longest match wins; a disallowed act stops the run. Court
decision paths (``*/kohtulahendid/*``, ``*/kohtuteave/*``) are never requested.
"""

from __future__ import annotations

import re
import shutil
import struct
import time
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

HOST = "www.riigiteataja.ee"
BASE = f"https://{HOST}"
ARCHIVE_PATH = re.compile(r"^avaandmed/ERT/xml\.(\d{4})\.zip$")
GLOBAL_ID = re.compile(r"^\d{12}$")
GENERATED = re.compile(r"genereeritud\s+(\d{4}-\d{2}-\d{2})\s+seisuga")
MIN_DELAY = 5.0
REFUSAL = re.compile(r"HTTP (401|403|407|429|451)\b")
TEXT_TYPES = ("terviktekst", "algtekst-terviktekst")  # tekstiliik of a consolidated text
CHUNK_BYTES = 64 * 1024 * 1024  # one range request of the stream mode
MAX_MEMBER_BYTES = 50_000_000  # an act whose XML is larger than this (uncompressed) is skipped and reported
RANGE_RETRIES = 5
TAIL_BYTES = 65536
# Slack after the compressed data when reading a member: the local header's name and extra fields.
LOCAL_SLACK = 1024

__all__ = [
    "FetchError",
    "RiigiTeatajaConnector",
    "RobotsRules",
    "ZipEntry",
    "locate_central_directory",
    "read_central_directory",
    "read_member",
]


# ---------------------------------------------------------------------------- robots.txt


class RobotsRules:
    """The ``User-agent: *`` rules of a robots.txt (RFC 9309 matching: ``*``, ``$``, longest match wins)."""

    def __init__(self, text: str):
        self.rules: list[tuple[bool, str, re.Pattern[str]]] = []  # (allow, pattern, regex)
        group_is_star = False
        in_rules = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            field, _, value = line.partition(":")
            field, value = field.strip().lower(), value.strip()
            if field == "user-agent":
                if in_rules:  # a new group starts after the previous group's rules
                    group_is_star, in_rules = False, False
                group_is_star = group_is_star or value == "*"
            elif field in ("allow", "disallow"):
                in_rules = True
                if group_is_star and value:
                    self.rules.append((field == "allow", value, self._regex(value)))

    @staticmethod
    def _regex(pattern: str) -> re.Pattern[str]:
        anchored = pattern.endswith("$")
        body = pattern[:-1] if anchored else pattern
        return re.compile("".join(".*" if ch == "*" else re.escape(ch) for ch in body) + ("$" if anchored else ""))

    def allowed(self, path: str) -> bool:
        best: tuple[int, bool] | None = None
        for allow, pattern, regex in self.rules:
            if regex.match(path):
                length = len(pattern)
                if best is None or length > best[0] or (length == best[0] and allow):
                    best = (length, allow)
        return True if best is None else best[1]


# ---------------------------------------------------------------------------- zip reading


@dataclass(frozen=True)
class ZipEntry:
    name: str
    method: int
    compressed_size: int
    size: int
    offset: int
    crc32: int


@dataclass(frozen=True)
class _Winner:
    """The latest consolidated text of a group up to the as-of date, while the archive is being read."""

    start: str  # kehtivuseAlgus, YYYY-MM-DD
    name: str
    entry: ZipEntry
    abbreviation: str
    why: str | None  # None: a text to ingest; otherwise "no text", "ended", "pinned" or "too large"
    retrieved_at: str


def locate_central_directory(tail: bytes) -> tuple[int, int, int]:
    """(offset, size, entries) of the central directory from the last bytes of a zip (zip64 aware)."""
    i = tail.rfind(b"PK\x05\x06")
    if i < 0 or len(tail) < i + 22:
        raise FetchError("no end of central directory record in the archive tail")
    _, _, _, _, entries, size, offset, _ = struct.unpack("<IHHHHIIH", tail[i : i + 22])
    if entries == 0xFFFF or size == 0xFFFFFFFF or offset == 0xFFFFFFFF:
        k = tail.rfind(b"PK\x06\x06", 0, i)
        if k < 0 or len(tail) < k + 56:
            raise FetchError("zip64 end of central directory record not found in the archive tail")
        _, _, _, _, _, _, _, entries, size, offset = struct.unpack("<IQHHIIQQQQ", tail[k : k + 56])
    return offset, size, entries


def iter_central_directory(directory: bytes, wanted: set[str] | None = None) -> Iterator[ZipEntry]:
    """The entries of a central directory (all, or only those named in ``wanted``), zip64 sizes and offsets resolved."""
    pos = 0
    while pos + 46 <= len(directory) and directory[pos : pos + 4] == b"PK\x01\x02":
        (_, _, _, _, method, _, _, crc, csize, usize, nlen, elen, clen, _, _, _, offset) = struct.unpack(
            "<IHHHHHHIIIHHHHHII", directory[pos : pos + 46]
        )
        name = directory[pos + 46 : pos + 46 + nlen].decode("utf-8", "replace")
        if wanted is None or name in wanted:
            extra = directory[pos + 46 + nlen : pos + 46 + nlen + elen]
            e = 0
            while e + 4 <= len(extra):
                tag, size = struct.unpack("<HH", extra[e : e + 4])
                if tag == 1:  # zip64 extended information: only the fields that were 0xFFFFFFFF
                    values, p = extra[e + 4 : e + 4 + size], 0
                    if usize == 0xFFFFFFFF:
                        usize, p = struct.unpack("<Q", values[p : p + 8])[0], p + 8
                    if csize == 0xFFFFFFFF:
                        csize, p = struct.unpack("<Q", values[p : p + 8])[0], p + 8
                    if offset == 0xFFFFFFFF:
                        offset, p = struct.unpack("<Q", values[p : p + 8])[0], p + 8
                e += 4 + size
            yield ZipEntry(name, method, csize, usize, offset, crc)
        pos += 46 + nlen + elen + clen


def read_central_directory(directory: bytes, wanted: set[str]) -> dict[str, ZipEntry]:
    """The entries named in ``wanted`` from a central directory (zip64 sizes and offsets resolved)."""
    return {entry.name: entry for entry in iter_central_directory(directory, wanted)}


# Metadata fields of an act (``<metaandmed>``) that the selection of acts in force reads.
META_FIELDS = (
    "dokumentLiik",
    "tekstiliik",
    "valjaandja",
    "kehtivuseAlgus",
    "kehtivuseLopp",
    "terviktekstiGrupiID",
    "globaalID",
    "lyhend",
)
HEAD_COMPRESSED = 16384  # compressed bytes of a member inspected for its metadata
HEAD_UNCOMPRESSED = 8000  # decompressed bytes searched for ``</metaandmed>``


def member_head(data: bytes | memoryview, entry: ZipEntry) -> bytes:
    """The first bytes of a member's XML (decompressed), from ``data`` starting at its local header.

    Only a prefix is decompressed: the metadata of an act is its first element, about 2 KB in."""
    if bytes(data[:4]) != b"PK\x03\x04":
        raise FetchError(f"{entry.name}: no local file header at offset {entry.offset}")
    nlen, elen = struct.unpack("<HH", bytes(data[26:30]))
    start = 30 + nlen + elen
    prefix = bytes(data[start : start + min(entry.compressed_size, HEAD_COMPRESSED)])
    if entry.method == 0:
        return prefix[:HEAD_UNCOMPRESSED]
    if entry.method != 8:
        raise FetchError(f"{entry.name}: unsupported zip compression method {entry.method}")
    try:
        return zlib.decompressobj(-15).decompress(prefix, HEAD_UNCOMPRESSED)
    except zlib.error as exc:
        raise FetchError(f"{entry.name}: cannot decompress: {exc}") from exc


def read_meta(head: bytes) -> dict[str, str]:
    """The fields of ``META_FIELDS`` in the ``<metaandmed>`` of an act's XML (dates cut to YYYY-MM-DD)."""
    text = head.decode("utf-8", "replace")
    end = text.find("</metaandmed>")
    if end < 0:
        return {}
    block = text[:end]
    out: dict[str, str] = {}
    after = text[end:]
    if re.search(r"<sisu\b[^>]*/>|<sisu\b[^>]*>\s*</sisu>", after):
        out["_empty"] = "1"  # a repealed act: its consolidated text has no content
    elif "<HTMLKonteiner" in after:
        out["_html"] = "1"  # an old text kept as one HTML fragment
    for name in META_FIELDS:
        m = re.search(rf"<{name}>([^<]*)</{name}>", block)
        if m:
            out[name] = m.group(1).strip()[:10] if name.startswith("kehtivuse") else m.group(1).strip()
    return out


def plan_chunks(entries: list[ZipEntry], directory_offset: int, chunk_bytes: int, large_bytes: int) -> list[Any]:
    """Split the archive into byte ranges of whole members: ``("chunk", start, end, [entries])`` for runs of ordinary
    members of at most ``chunk_bytes`` and ``("large", start, end, [entry])`` for a member of more than ``large_bytes``
    compressed, whose head is read first so that it is not downloaded unless it is wanted. ``end`` is inclusive."""
    ordered = sorted(entries, key=lambda e: e.offset)
    plan: list[Any] = []
    run: list[ZipEntry] = []
    run_start = 0

    def flush(end: int) -> None:
        nonlocal run
        if run:
            plan.append(("chunk", run_start, end, run))
            run = []

    for i, entry in enumerate(ordered):
        end = (ordered[i + 1].offset if i + 1 < len(ordered) else directory_offset) - 1
        if entry.compressed_size > large_bytes:
            flush(entry.offset - 1)
            plan.append(("large", entry.offset, end, [entry]))
            continue
        if run and end - run_start + 1 > chunk_bytes:
            flush(entry.offset - 1)
        if not run:
            run_start = entry.offset
        run.append(entry)
    flush(directory_offset - 1)
    return plan


def read_member(data: bytes, entry: ZipEntry) -> bytes:
    """The uncompressed bytes of a member from ``data`` (starting at its local header); CRC-32 verified."""
    if data[:4] != b"PK\x03\x04":
        raise FetchError(f"{entry.name}: no local file header at offset {entry.offset}")
    nlen, elen = struct.unpack("<HH", data[26:30])
    if data[30 : 30 + nlen].decode("utf-8", "replace") != entry.name:
        raise FetchError(f"{entry.name}: the local header names another member")
    start = 30 + nlen + elen
    body = data[start : start + entry.compressed_size]
    if len(body) != entry.compressed_size:
        raise FetchError(f"{entry.name}: truncated member ({len(body)} of {entry.compressed_size} bytes)")
    if entry.method == 8:
        try:
            raw = zlib.decompress(body, -15)
        except zlib.error as exc:
            raise FetchError(f"{entry.name}: cannot decompress: {exc}") from exc
    elif entry.method == 0:
        raw = body
    else:
        raise FetchError(f"{entry.name}: unsupported zip compression method {entry.method}")
    if len(raw) != entry.size or zlib.crc32(raw) != entry.crc32:
        raise FetchError(f"{entry.name}: size or CRC-32 differs from the archive's central directory")
    return raw


# ---------------------------------------------------------------------------- connector


class RiigiTeatajaConnector:
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
        # Acts the stream mode skipped (reported as run errors by the pipeline) and what it read.
        self.fetch_errors: list[str] = []
        self.stream_stats: dict[str, int] = {}
        self.robots_skipped: list[str] = []

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if str(manifest.get("base_url", "")).rstrip("/") != BASE:
            raise ManifestError(f"base_url must be {BASE}")
        if not ARCHIVE_PATH.match(str(manifest.get("archive", ""))):
            raise ManifestError("archive must be avaandmed/ERT/xml.<year>.zip")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        if float(manifest.get("crawl_delay_seconds", MIN_DELAY)) < MIN_DELAY:
            raise ManifestError(f"crawl_delay_seconds must be at least {MIN_DELAY:g}")
        works = manifest.get("works") or []
        select = manifest.get("select")
        if not works and not select:
            raise ManifestError("the manifest lists no works and selects none")
        if select is not None:
            self._check_select(select)
        seen: set[str] = set()
        for work in works:
            if not str(work.get("group_id", "")).isdigit() or not work.get("abbreviation"):
                raise ManifestError(f"work {work.get('name')!r}: group_id (digits) and abbreviation are required")
            versions = work.get("versions") or []
            if not versions:
                raise ManifestError(f"work {work.get('name')!r} lists no versions")
            for version in versions:
                global_id = str(version)
                if not GLOBAL_ID.match(global_id):
                    raise ManifestError(f"invalid globaalID {global_id!r} (12 digits)")
                if global_id in seen:
                    raise ManifestError(f"{global_id} is listed twice")
                seen.add(global_id)
        return manifest

    @staticmethod
    def _check_select(select: Any) -> None:
        if not isinstance(select, dict) or not select.get("document_types"):
            raise ManifestError("select: document_types (a list of dokumentLiik values) is required")
        types = select["document_types"]
        if not isinstance(types, list) or not all(
            isinstance(t, str) and re.fullmatch(r"[\w -]{2,40}", t) for t in types
        ):
            raise ManifestError("select: document_types must be a list of dokumentLiik words")
        as_of = select.get("as_of", "archive")
        if as_of != "archive":
            try:
                date.fromisoformat(str(as_of))
            except ValueError as exc:
                raise ManifestError("select: as_of must be 'archive' or a date (YYYY-MM-DD)") from exc
        part = select.get("part")
        if part is not None and not (
            isinstance(part, str) and (m := re.fullmatch(r"(\d+)/(\d+)", part)) and 1 <= int(m[1]) <= int(m[2]) <= 1000
        ):
            raise ManifestError("select: part must be 'i/n' with 1 <= i <= n (a share of the archive)")
        if select.get("exclude_issuers") is not None:
            try:
                re.compile(str(select["exclude_issuers"]))
            except re.error as exc:
                raise ManifestError(f"select: exclude_issuers is not a regular expression ({exc})") from exc
        for key in ("max_member_bytes", "chunk_bytes"):
            if key in select and not (isinstance(select[key], int) and select[key] > 0):
                raise ManifestError(f"select: {key} must be a positive integer")

    def _get(self, url: str, **kwargs: Any) -> tuple[bytes, dict[str, Any]]:
        assert self.http is not None
        got = self.http.get(url, **kwargs)
        if got is None:
            raise FetchError(f"{url}: not found (404)")
        return got

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
        archive_url = f"{BASE}/{manifest['archive']}"
        self.fetch_errors = []

        robots_bytes, _ = self._get(f"{BASE}/robots.txt")
        robots = RobotsRules(robots_bytes.decode("utf-8", "replace"))
        if not robots.allowed(f"/{manifest['archive']}"):
            raise FetchError(f"robots.txt disallows /{manifest['archive']}")
        works = manifest.get("works") or []
        ids = [str(v) for w in works for v in w["versions"]]
        for global_id in ids:
            # The act's web page is what robots.txt names (``*/akt/<id>/*``); the entry is in a bulk
            # archive, but an act the publisher asks robots not to crawl is not ingested either.
            if not (robots.allowed(f"/akt/{global_id}") and robots.allowed(f"/akt/{global_id}/")):
                raise FetchError(f"robots.txt disallows /akt/{global_id}: not ingested")

        generated_bytes, _ = self._get(f"{BASE}/avaandmed/ERT/LOEMIND.txt")
        match = GENERATED.search(generated_bytes.decode("utf-8", "replace"))
        generated = date.fromisoformat(match.group(1)) if match else None

        tail, _ = self._get(
            archive_url, cache_key=f"{Path(manifest['archive']).name}.tail", headers={"Range": f"bytes=-{TAIL_BYTES}"}
        )
        offset, size, entries = locate_central_directory(tail)
        directory, _ = self._get(
            archive_url,
            cache_key=f"{Path(manifest['archive']).name}.central-directory@{offset}-{size}",
            headers={"Range": f"bytes={offset}-{offset + size - 1}"},
        )
        if len(directory) != size:
            raise FetchError(f"central directory: expected {size} bytes, received {len(directory)}")
        members = read_central_directory(directory, {f"{i}.xml" for i in ids}) if ids else {}
        for work in works:
            for global_id in map(str, work["versions"]):
                name = f"{global_id}.xml"
                entry = members.get(name)
                if entry is None:
                    raise FetchError(f"{name} is not in {archive_url} ({entries} entries)")
                last = entry.offset + 30 + len(name) + LOCAL_SLACK + entry.compressed_size - 1
                data, meta = self._get(
                    archive_url, cache_key=f"{name}@{entry.offset}", headers={"Range": f"bytes={entry.offset}-{last}"}
                )
                content = read_member(data, entry)
                if not re.match(rb"\A(\xef\xbb\xbf)?<\?xml[^>]*\?>\s*<oigusakt[\s>]", content):
                    raise FetchError(f"{name}: not a Riigi Teataja act XML (root element)")
                yield RawDocument(
                    external_id=f"ee-rt:{global_id}",
                    uri=f"{archive_url}#{name}",
                    media_type="application/xml",
                    content=content,
                    retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                    metadata={
                        "global_id": global_id,
                        "group_id": str(work["group_id"]),
                        "abbreviation": work["abbreviation"],
                        "act_page": f"{BASE}/akt/{global_id}",
                        "archive": archive_url,
                        "archive_generated": generated.isoformat() if generated else None,
                        "member": name,
                        "member_offset": entry.offset,
                        "member_crc32": f"{entry.crc32:08x}",
                        "member_size": entry.size,
                        "cached_fetch": meta,
                    },
                )
        if manifest.get("select"):
            yield from self._stream(manifest, archive_url, robots, generated, directory, offset, set(ids))

    # ------------------------------------------------------------------ the acts in force, from the whole archive

    def _range(self, url: str, start: int, end: int, key: str) -> tuple[bytes, dict[str, Any]]:
        """One byte range of the archive. The server now and then answers a small range with the whole file (HTTP 200
        instead of 206); PoliteHttp stops before reading the body, and the request is repeated after a pause."""
        assert self.http is not None
        for attempt in range(RANGE_RETRIES):
            try:
                return self._get(url, cache_key=key, headers={"Range": f"bytes={start}-{end}"})
            except FetchError as exc:
                if "range request was not honoured" not in str(exc) or attempt == RANGE_RETRIES - 1:
                    raise
                self._sleep(10.0 * (attempt + 1))
        raise AssertionError("unreachable")  # pragma: no cover

    def _drop(self, url: str, key: str) -> None:
        """A range of the archive is not kept once used (the cache would grow to the archive's size, 43 GB); an
        offline run, which only reads a prepared cache, leaves it as it is."""
        if self.offline or self.http is None:
            return
        path = self.http.cache_path(url, key)
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".meta.json").unlink(missing_ok=True)

    def _stream(
        self,
        manifest: dict[str, Any],
        archive_url: str,
        robots: RobotsRules,
        generated: date | None,
        directory: bytes,
        directory_offset: int,
        pinned: set[str],
    ) -> Iterator[RawDocument]:
        """The consolidated text in force on the as-of date of every act, found by reading the whole archive.

        The archive is read in ranges of ``chunk_bytes`` (one request each, 5 s apart) and the metadata of every
        member is checked. A group (``terviktekstiGrupiID``) is one act with all its consolidated texts; the text in
        force on the date is the group's text with the latest ``kehtivuseAlgus`` up to that date. The end of a
        superseded text (``kehtivuseLopp``) is not reliable (Maksukorralduse seadus has older texts without one), so
        the winner is chosen among all the texts of the group, not by the end date. The winner is kept (compressed, in a
        scratch file under the cache) while the archive is read and replaced when a later text of the group turns up;
        when the archive has been read, the winners that are in force and have a text are delivered. A winner that is a
        repealed act's empty text, an old HTML fragment, ended before the date, over ``max_member_bytes`` or disallowed
        by robots.txt means the act is not ingested (and no older text of it is).

        Selection (``select`` in the manifest): ``tekstiliik`` is a consolidated text, ``dokumentLiik`` one of
        ``document_types``, the issuer does not match ``exclude_issuers``. ``part: "i/n"`` reads a share of the ranges
        and chooses the winners among the texts it holds: for trials, not for the corpus."""
        select = manifest["select"]
        as_of_setting = select.get("as_of", "archive")
        as_of = generated if as_of_setting == "archive" else date.fromisoformat(str(as_of_setting))
        if as_of is None:
            raise FetchError("select.as_of is 'archive' but LOEMIND.txt names no generation date")
        as_of_iso = as_of.isoformat()
        types = set(select["document_types"])
        text_types = set(select.get("text_types") or TEXT_TYPES)
        exclude = re.compile(str(select["exclude_issuers"])) if select.get("exclude_issuers") else None
        max_bytes = int(select.get("max_member_bytes", MAX_MEMBER_BYTES))
        chunk_bytes = int(select.get("chunk_bytes", CHUNK_BYTES))
        archive_name = Path(manifest["archive"]).name
        plan = plan_chunks(list(iter_central_directory(directory)), directory_offset, chunk_bytes, chunk_bytes // 8)
        if select.get("part"):
            i, n = (int(x) for x in str(select["part"]).split("/"))
            plan = plan[(i - 1) * len(plan) // n : i * len(plan) // n]
        self.stream_stats = {"requests": 0, "members": 0, "groups": 0, "selected": 0, "bytes": 0}
        spool = self.cache_dir / HOST / "spool"
        shutil.rmtree(spool, ignore_errors=True)  # left by an interrupted run
        spool.mkdir(parents=True)

        def spool_path(group: str) -> Path:
            return spool / (re.sub(r"\W", "_", group) + ".member")

        best: dict[str, _Winner] = {}

        for kind, start, end, members in plan:
            key = f"{archive_name}.{kind}@{start}-{end}"
            try:
                last_byte = end if kind == "chunk" else min(end, start + HEAD_COMPRESSED + 4096)
                data, meta = self._range(archive_url, start, last_byte, key)
                self.stream_stats["requests"] += 1
                self.stream_stats["bytes"] += len(data)
                view = memoryview(data)
                for idx, entry in enumerate(members):
                    self.stream_stats["members"] += 1
                    local = entry.offset - start
                    span = (members[idx + 1].offset if idx + 1 < len(members) else end + 1) - entry.offset
                    head = read_meta(member_head(view[local : local + span], entry))
                    if head.get("tekstiliik") not in text_types or head.get("dokumentLiik") not in types:
                        continue
                    if exclude is not None and exclude.search(head.get("valjaandja", "")):
                        continue
                    first = head.get("kehtivuseAlgus")
                    if not first or first > as_of_iso:
                        continue  # no start, or a text that starts after the date
                    group = head.get("terviktekstiGrupiID") or entry.name
                    current = best.get(group)
                    if current is not None and (first, entry.name) <= (current.start, current.name):
                        continue  # an earlier text of a group that already has a later one
                    last = head.get("kehtivuseLopp")
                    why: str | None = None
                    if head.get("_empty") or head.get("_html"):
                        why = "no text"  # a repealed act's empty text, or an old HTML fragment
                    elif last and last < as_of_iso:
                        why = "ended"
                    elif entry.name.removesuffix(".xml") in pinned:
                        why = "pinned"  # delivered with its work
                    elif entry.size > max_bytes:
                        why = "too large"
                    spool_path(group).unlink(missing_ok=True)
                    if why is None:
                        if kind == "large":  # only the head was read: the whole member is fetched now
                            member_key = f"{archive_name}.member@{entry.offset}-{end}"
                            full, body_meta = self._range(archive_url, entry.offset, end, member_key)
                            self._drop(archive_url, member_key)
                            record, when = full, body_meta["retrieved_at"]
                        else:
                            record, when = bytes(view[local : local + span]), meta["retrieved_at"]
                        spool_path(group).write_bytes(record)
                    else:
                        when = meta["retrieved_at"]
                    best[group] = _Winner(first, entry.name, entry, head.get("lyhend", ""), why, when)
            except FetchError as exc:
                if REFUSAL.search(str(exc)):
                    raise
                self.fetch_errors.append(f"ee-rt: bytes {start}-{end} of {archive_name}: {exc}")
            finally:
                self._drop(archive_url, key)

        self.stream_stats["groups"] = len(best)
        robots_skipped: list[str] = []
        too_large: list[str] = []
        for group, winner in sorted(best.items(), key=lambda kv: kv[1].name):
            file = spool_path(group)
            entry = winner.entry
            global_id = entry.name.removesuffix(".xml")  # the member's name (12 digits, zero-padded when old)
            try:
                if winner.why == "too large":
                    too_large.append(global_id)
                if winner.why is not None or global_id in pinned:
                    continue
                ids = {global_id, str(int(global_id))} if global_id.isdigit() else {global_id}
                if not all(robots.allowed(f"/akt/{i}") and robots.allowed(f"/akt/{i}/") for i in ids):
                    robots_skipped.append(global_id)
                    continue
                content = read_member(file.read_bytes(), entry)
                if not re.match(rb"\A(\xef\xbb\xbf)?<\?xml[^>]*\?>\s*<oigusakt[\s>]", content):
                    raise FetchError(f"{entry.name}: not a Riigi Teataja act XML (root element)")
            except FetchError as exc:
                self.fetch_errors.append(f"ee-rt:{global_id}: {exc}")
                continue
            finally:
                file.unlink(missing_ok=True)
            self.stream_stats["selected"] += 1
            yield RawDocument(
                external_id=f"ee-rt:{global_id}",
                uri=f"{archive_url}#{entry.name}",
                media_type="application/xml",
                content=content,
                retrieved_at=datetime.fromisoformat(winner.retrieved_at),
                metadata={
                    "global_id": global_id,
                    "group_id": group,
                    "abbreviation": winner.abbreviation,
                    "act_page": f"{BASE}/akt/{global_id}",
                    "archive": archive_url,
                    "archive_generated": generated.isoformat() if generated else None,
                    "selected": f"latest consolidated text of its group up to {as_of_iso}, in force on that date",
                    "member": entry.name,
                    "member_offset": entry.offset,
                    "member_crc32": f"{entry.crc32:08x}",
                    "member_size": entry.size,
                },
            )
        shutil.rmtree(spool, ignore_errors=True)
        self.robots_skipped = robots_skipped
        if too_large:
            self.fetch_errors.append(
                f"ee-rt: {len(too_large)} acts in force are larger than max_member_bytes ({max_bytes}) and were not "
                f"ingested: {', '.join(too_large[:20])}"
            )
        if robots_skipped:
            self.fetch_errors.append(
                f"ee-rt: {len(robots_skipped)} acts in force were not ingested because robots.txt disallows their page "
                f"(*/akt/<id>/*); not errors, a deliberate exclusion"
            )
