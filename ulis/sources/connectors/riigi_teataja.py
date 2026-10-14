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


def read_central_directory(directory: bytes, wanted: set[str]) -> dict[str, ZipEntry]:
    """The entries named in ``wanted`` from a central directory (zip64 sizes and offsets resolved)."""
    found: dict[str, ZipEntry] = {}
    pos = 0
    while pos + 46 <= len(directory) and directory[pos : pos + 4] == b"PK\x01\x02":
        (_, _, _, _, method, _, _, crc, csize, usize, nlen, elen, clen, _, _, _, offset) = struct.unpack(
            "<IHHHHHHIIIHHHHHII", directory[pos : pos + 46]
        )
        name = directory[pos + 46 : pos + 46 + nlen].decode("utf-8", "replace")
        if name in wanted:
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
            found[name] = ZipEntry(name, method, csize, usize, offset, crc)
        pos += 46 + nlen + elen + clen
    return found


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
        if not works:
            raise ManifestError("the manifest lists no works")
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

        robots_bytes, _ = self._get(f"{BASE}/robots.txt")
        robots = RobotsRules(robots_bytes.decode("utf-8", "replace"))
        if not robots.allowed(f"/{manifest['archive']}"):
            raise FetchError(f"robots.txt disallows /{manifest['archive']}")
        ids = [str(v) for w in manifest["works"] for v in w["versions"]]
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
        members = read_central_directory(directory, {f"{i}.xml" for i in ids})
        for work in manifest["works"]:
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
