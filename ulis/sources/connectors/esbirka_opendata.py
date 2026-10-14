"""Czech Collection of Laws (e-Sbírka): the publisher's open-data dumps, sliced to the acts a manifest selects.

The Ministry of the Interior publishes the whole corpus of e-Sbírka as daily regenerated JSON files under
``https://opendata.eselpoint.gov.cz/datove-sady-esbirka/`` (a plain Apache directory listing; no robots.txt).
Four of them are needed to rebuild the text of a version of an act:

* ``001PravniAktZneni`` - one record per *version* ("znění") of every act: ELI, the dates it takes effect and
  ceases to have effect, the kind of version (``KONSOL`` consolidated, ``VYHLZNE`` as promulgated), the
  collection (``sb`` = Sbírka zákonů), the ids of its metadata and of its text document;
* ``003PravniAktZneniFragment`` - the fragments (parts: sections, paragraphs, letters, headings...) of every
  version: parent, hierarchy, citation label, a date from which the fragment takes effect when it differs from
  the version's, and the id of the fragment text;
* ``004PravniAktFragment`` - the text of each fragment (with a little inline markup) and its type;
* ``006PravniAktMetadata`` - title, short title, kind, dates of approval and repeal of an act.

Together they are about 2 GB compressed (30 GB of JSON). ULIS downloads each file once (with an identifying
User-Agent, HTTPS, resumable, cached under ``<cache>/opendata.eselpoint.gov.cz/``), reads it as a stream and keeps
only the records of the selected versions in a small SQLite *slice* next to the cache (never the whole corpus in
memory). The manifest selects acts (see :func:`parse_selection`); nothing else is read into the slice.

``fetch()`` then yields one :class:`RawDocument` per selected version, oldest first within an act (whole-instrument
snapshots are recorded oldest first, see ``ulis.ingestion.snapshots``). A document is a compact JSON object whose
values are copied unchanged from the records: ``columns`` names the source keys of every fragment row.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError

HOST = "opendata.eselpoint.gov.cz"
BASE_URL = f"https://{HOST}/datove-sady-esbirka/"
DATASETS = {
    "versions": "001PravniAktZneni.json.gz",
    "version_fragments": "003PravniAktZneniFragment.json.gz",
    "fragments": "004PravniAktFragment.json.gz",
    "metadata": "006PravniAktMetadata.json.gz",
}
SLICE_VERSION = 3
SEPARATOR = b"\n},\n{\n"
ITEMS_KEY = '"položky"'.encode()
FRAGMENT_LIST_KEY = '  "právní-akt-znění-fragment" : ['.encode()
AFTER_FRAGMENT_LIST_KEY = '\n  "právní-akt-související-dokument"'.encode()
ACT_ID = re.compile(r"^(sb)/([0-9]{4})/([0-9]{1,4})$")
ELI_IRI = re.compile(r"^esel-esb:eli/cz/([a-z0-9]+)/([0-9]{4})/([0-9]+)(?:/([0-9]{4}-[0-9]{2}-[0-9]{2}))?$")
# Source keys copied, in this order, into the rows of a raw document's ``fragments``.
FRAGMENT_COLUMNS = (
    "eli",  # znění-fragment-eli
    "parent",  # znění-fragment-předek (ELI of the parent fragment)
    "label_citation",  # znění-fragment-citace
    "label_number",  # znění-fragment-označení-uzlu
    "label_text",  # znění-fragment-označení-uzlu-text
    "url",  # znění-fragment-url
    "hierarchy",  # znění-fragment-hierarchie ("/1/2/": position among the siblings at every level)
    "differs_from",  # znění-fragment-odlišná-účinnost
    "selected_from",  # znění-fragment-vybrané-od
    "selected_to",  # znění-fragment-vybrané-do
    "fragment_id",  # právní-akt-fragment.fragment-id
    "type",  # cis-esb-typ-fragmentu-položka (004)
    "text",  # fragment-text (004)
)
VERSION_KEYS = (
    "iri",
    "akt-citace",
    "akt-název-vyhlášený",
    "akt-iri",
    "znění-id",
    "znění-dokument-id",
    "znění-datum-čas-poslední-změny",
    "znění-datum-účinnosti-od",
    "znění-datum-účinnosti-do",
    "znění-eli",
    "znění-je-zrušen",
    "znění-nikdy-nebyl-účinný",
    "cis-esb-sbírka-položka",
    "cis-esb-typ-znění-položka",
    "cis-esb-stav-vyhlášení-aktu-položka",
    "znění-účinnost-textem",
)
METADATA_KEYS = (
    "metadata-id",
    "metadata-cislo-predpisu",
    "metadata-rok-předpisu",
    "metadata-citace",
    "metadata-název",
    "metadata-název-citace",
    "metadata-název-zkrácený",
    "metadata-datum-schválení",
    "metadata-datum-účinnosti-od",
    "metadata-datum-účinnosti-do",
    "metadata-datum-platnosti-do",
    "metadata-datum-zrušení",
    "metadata-eli",
    "metadata-účinnost-textem",
    "metadata-zrušení-textem",
    "cis-esb-územní-platnost-položka",
    "cis-esb-podtyp-právní-akt-položka",
    "cis-esb-typ-právní-akt-položka",
    "cis-esb-druh-aktu-položka",
)

__all__ = [
    "BASE_URL",
    "DATASETS",
    "EsbirkaOpenDataConnector",
    "Selection",
    "iter_records",
    "parse_selection",
]


@dataclass(frozen=True)
class Selection:
    """What a manifest asks for: the acts with their whole history, and the other acts' current version."""

    as_of: date
    collections: tuple[str, ...]
    full_history: frozenset[str]  # act ids "sb/2012/89": every version up to ``as_of``
    in_force: bool  # also the version in force on ``as_of`` of every other act of the collections
    skip: frozenset[str] = field(default_factory=frozenset)  # act ids never taken
    current: frozenset[str] = field(default_factory=frozenset)  # act ids: only the version in force on ``as_of``
    years: tuple[int, int] | None = None  # restricts ``in_force`` to acts numbered in these years (inclusive)

    def fingerprint(self) -> str:
        payload = json.dumps(
            [
                self.as_of.isoformat(),
                list(self.collections),
                sorted(self.full_history),
                self.in_force,
                sorted(self.skip),
                sorted(self.current),
                list(self.years) if self.years else None,
                SLICE_VERSION,
            ]
        )
        return hashlib.sha256(payload.encode()).hexdigest()


def parse_selection(manifest: dict[str, Any]) -> Selection:
    sel = manifest.get("selection")
    if not isinstance(sel, dict):
        raise ManifestError("the manifest has no 'selection' block")
    try:
        as_of = date.fromisoformat(str(sel.get("as_of")))
    except ValueError as exc:
        raise ManifestError("selection.as_of must be an ISO date") from exc
    collections = tuple(sel.get("collections") or ["sb"])
    if collections != ("sb",):
        raise ManifestError("only the collection 'sb' (Sbírka zákonů) is supported")

    def act_ids(key: str) -> frozenset[str]:
        out: set[str] = set()
        for raw in sel.get(key) or []:
            act = str(raw["act"] if isinstance(raw, dict) else raw).strip()
            if not ACT_ID.match(act):
                raise ManifestError(f"invalid act id {act!r} in selection.{key} (expected sb/<year>/<number>)")
            out.add(act)
        return frozenset(out)

    full = act_ids("full_history")
    current = act_ids("current")
    in_force = bool(sel.get("in_force", False))
    if not full and not current and not in_force:
        raise ManifestError("the selection names no act and does not ask for acts in force")
    years = None
    if sel.get("in_force_years"):
        try:
            low, high = (int(y) for y in sel["in_force_years"])
        except (TypeError, ValueError) as exc:
            raise ManifestError("selection.in_force_years must be [first year, last year]") from exc
        if not 1918 <= low <= high <= 2100:
            raise ManifestError("selection.in_force_years is out of range")
        years = (low, high)
    return Selection(as_of, collections, full, in_force, act_ids("skip"), current, years)


def gunzip_chunks(path: Path, size: int = 1 << 23) -> Iterator[bytes]:
    with gzip.open(path, "rb") as handle:
        while True:
            chunk = handle.read(size)
            if not chunk:
                return
            yield chunk


def iter_records(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """The items of the ``"položky"`` array of a dump as JSON texts, without parsing the whole file.

    The dumps are pretty printed: every item starts with ``{`` and ends with ``}`` at column 0 and no raw newline
    occurs inside a JSON string, so ``\\n},\\n{\\n`` only ever separates two items.
    """
    tail = b""
    started = False
    for chunk in chunks:
        buf = tail + chunk
        if not started:
            marker = buf.find(ITEMS_KEY)
            if marker < 0:
                tail = buf
                continue
            start = buf.find(b"{\n", buf.find(b"[", marker))
            if start < 0:
                tail = buf
                continue
            buf = buf[start + 2 :]
            started = True
        parts = buf.split(SEPARATOR)
        tail = parts.pop()
        for part in parts:
            yield b"{\n" + part + b"\n}"
    if not started:
        raise FetchError("not an e-Sbírka dump: no \"položky\" array found")
    end = tail.rstrip()
    suffix = re.search(rb"\n\}\s*\]\s*\}$", end)
    if suffix is None:
        raise FetchError("the dump ends in the middle of an item (truncated download?)")
    body = end[: suffix.start()]
    if body.strip():
        yield b"{\n" + body + b"\n}"


def _split_version(record: bytes) -> dict[str, Any]:
    """Head and tail of a 001 record as one dict; the long list of fragment IRIs in between is not parsed."""
    start = record.find(FRAGMENT_LIST_KEY)
    end = record.find(AFTER_FRAGMENT_LIST_KEY, max(start, 0))
    if start < 0 or end < 0:
        return json.loads(record)
    head = record[:start].rstrip()
    tail = record[end + 1 :]
    out: dict[str, Any] = json.loads(head.rstrip(b",") + b"\n}")
    out.update(json.loads(b"{\n" + tail))
    return out


def _int_after(record: bytes, key: bytes) -> int | None:
    i = record.find(key)
    if i < 0:
        return None
    j = i + len(key)
    k = j
    while k < len(record) and 48 <= record[k] <= 57:
        k += 1
    return int(record[j:k]) if k > j else None


def _eli_path(iri: str | None) -> str | None:
    """``esel-esb:eli/cz/sb/2012/89`` -> ``/eli/cz/sb/2012/89`` (the form of the ``...-eli`` keys)."""
    return "/" + iri.removeprefix("esel-esb:") if iri else None


def _act_of(iri: str) -> str | None:
    m = ELI_IRI.match(iri)
    return f"{m.group(1)}/{m.group(2)}/{m.group(3)}" if m else None


def _wanted(sel: Selection, act: str, version: dict[str, Any]) -> bool:
    """Whether this version of ``act`` is among those the selection asks for (as-promulgated texts aside)."""
    if version.get("znění-je-zrušen") or version.get("znění-nikdy-nebyl-účinný"):
        return False
    start = version.get("znění-datum-účinnosti-od")
    end = version.get("znění-datum-účinnosti-do")
    if not start or date.fromisoformat(start) > sel.as_of:
        return False
    if act in sel.full_history:
        return True
    if end and date.fromisoformat(end) < sel.as_of:
        return False
    if act in sel.current:
        return True
    year = int(act.split("/")[1])
    return sel.in_force and (sel.years is None or sel.years[0] <= year <= sel.years[1])


class EsbirkaOpenDataConnector:
    def __init__(
        self,
        manifest_path: Path,
        expected_source_id: str,
        cache_dir: Path,
        *,
        offline: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        files: dict[str, Path] | None = None,
    ):
        self.manifest_path = manifest_path
        self.expected_source_id = expected_source_id
        self.cache_dir = cache_dir
        self.offline = offline
        self._sleep = sleep
        # Local copies of the dumps, by dataset name (tests and offline runs); otherwise they are downloaded.
        self._files = files
        self.fetch_errors: list[str] = []
        self.requests_made = 0
        self._last_request = 0.0

    # ------------------------------------------------------------------ manifest

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if manifest.get("base_url", BASE_URL).rstrip("/") + "/" != BASE_URL:
            raise ManifestError(f"base_url must be {BASE_URL}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        if float(manifest.get("crawl_delay_seconds", 5)) < 5:
            raise ManifestError("crawl_delay_seconds must be at least 5")
        parse_selection(manifest)
        return manifest

    # ------------------------------------------------------------------ downloads

    def _download(self, name: str, manifest: dict[str, Any]) -> Path:
        """The dump ``name`` in the cache, downloaded once (resumable); returns its path."""
        filename = DATASETS[name]
        dest = self.cache_dir / HOST / "datove-sady-esbirka" / filename
        meta_file = dest.with_name(filename + ".meta.json")
        if dest.exists() and meta_file.exists():
            return dest
        if self.offline:
            raise FetchError(f"offline and not cached: {BASE_URL}{filename}")
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        part = dest.with_name(filename + ".part")
        url = BASE_URL + filename
        for attempt in range(5):
            wait = self._last_request + float(manifest.get("crawl_delay_seconds", 5)) - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last_request = time.monotonic()
            self.requests_made += 1
            have = part.stat().st_size if part.exists() else 0
            headers = {"User-Agent": manifest["user_agent"]}
            if have:
                headers["Range"] = f"bytes={have}-"
            request = urllib.request.Request(url, headers=headers)  # noqa: S310 - https only, one host
            try:
                with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
                    if response.status == 200 and have:
                        have = 0  # the server ignored the range: start again
                    total = int(response.headers.get("Content-Length", "0")) + have
                    with part.open("ab" if have else "wb") as out:
                        while True:
                            block = response.read(1 << 20)
                            if not block:
                                break
                            out.write(block)
                    if total and part.stat().st_size != total:
                        raise FetchError(f"{url}: incomplete download ({part.stat().st_size} of {total} bytes)")
                    meta = {
                        "url": url,
                        "status": response.status,
                        "etag": response.headers.get("ETag"),
                        "last_modified": response.headers.get("Last-Modified"),
                        "content_length": part.stat().st_size,
                        "retrieved_at": datetime.now(UTC).isoformat(),
                    }
                part.replace(dest)
                meta_file.write_text(json.dumps(meta))
                return dest
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 4:
                    self._sleep(float(manifest.get("crawl_delay_seconds", 5)) * 2**attempt)
                    continue
                raise FetchError(f"{url}: HTTP {exc.code} (not retried: a refusal is respected)") from exc
            except (urllib.error.URLError, TimeoutError, FetchError, ConnectionError) as exc:
                if attempt < 4:
                    self._sleep(float(manifest.get("crawl_delay_seconds", 5)) * 2**attempt)
                    continue
                raise FetchError(f"{url}: {exc}") from exc
        raise FetchError(url)  # pragma: no cover

    def _path(self, name: str, manifest: dict[str, Any]) -> Path:
        if self._files is not None:
            return self._files[name]
        return self._download(name, manifest)

    def _file_meta(self, name: str, path: Path) -> dict[str, Any]:
        meta_file = path.with_name(path.name + ".meta.json")
        if meta_file.exists():
            meta = json.loads(meta_file.read_text())
            return {k: meta.get(k) for k in ("url", "etag", "last_modified", "content_length", "retrieved_at")}
        return {"url": BASE_URL + DATASETS[name], "size": path.stat().st_size}

    # ------------------------------------------------------------------ slice

    def _slice_path(self, selection: Selection) -> Path:
        return self.cache_dir / HOST / f"slice-{selection.fingerprint()[:16]}.sqlite"

    def build_slice(self, manifest: dict[str, Any]) -> Path:
        selection = parse_selection(manifest)
        path = self._slice_path(selection)
        if path.exists():
            db = sqlite3.connect(path)
            try:
                done = db.execute("select value from meta where key='complete'").fetchone()
            finally:
                db.close()
            if done and done[0] == "1":
                return path
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_name(path.name + ".building")
        tmp.unlink(missing_ok=True)
        db = sqlite3.connect(tmp)
        try:
            db.executescript(
                """
                create table meta (key text primary key, value text);
                create table version (doc_id integer primary key, act text, start text, data text);
                create table vfrag (doc_id integer, seq integer, fid integer, data text);
                create index vfrag_doc on vfrag (doc_id, seq);
                create table ftext (fid integer primary key, data text);
                create table metadata (mid integer primary key, data text);
                """
            )
            files = {name: self._path(name, manifest) for name in DATASETS}
            doc_ids = self._pass_versions(db, files["versions"], selection)
            fids = self._pass_version_fragments(db, files["version_fragments"], doc_ids)
            self._pass_fragments(db, files["fragments"], fids)
            self._pass_metadata(db, files["metadata"])
            db.executemany(
                "insert into meta values (?, ?)",
                [
                    ("selection", selection.fingerprint()),
                    ("as_of", selection.as_of.isoformat()),
                    ("files", json.dumps({n: self._file_meta(n, p) for n, p in files.items()})),
                    ("complete", "1"),
                ],
            )
            db.commit()
        finally:
            db.close()
        tmp.replace(path)
        return path

    def _pass_versions(self, db: sqlite3.Connection, path: Path, selection: Selection) -> set[int]:
        """Select the versions (see :class:`Selection`) and store them in the slice; returns their document ids.

        Every act has an as-promulgated version (``VYHLZNE``, ELI date ``0000-00-00``) that starts on the day the
        act's first consolidated version (``KONSOL``) starts, with no end date of its own; it is only kept for an
        act that has no consolidated version starting that day.
        """
        chosen: dict[int, tuple[str, str, str]] = {}  # document id -> (act, start, JSON)
        promulgated: dict[str, tuple[int, str, str, dict[str, Any]]] = {}
        starts: dict[str, set[str]] = {}
        for record in iter_records(gunzip_chunks(path)):
            iri_pos = record.find(b'"akt-iri" : "')
            if iri_pos < 0:
                continue
            end = record.find(b'"', iri_pos + 13)
            act = _act_of(record[iri_pos + 13 : end].decode("utf8"))
            if act is None or act in selection.skip or act.split("/", 1)[0] not in selection.collections:
                continue
            if act not in selection.full_history and act not in selection.current and not selection.in_force:
                continue
            version = _split_version(record)
            if not _wanted(selection, act, version):
                continue
            doc_id = version["znění-dokument-id"]
            start = version["znění-datum-účinnosti-od"]
            data = {k: version.get(k) for k in VERSION_KEYS}
            data["metadata-id"] = (version.get("právní-akt-metadata") or {}).get("metadata-id")
            if version.get("cis-esb-typ-znění-položka") == "VYHLZNE":
                promulgated[act] = (doc_id, start, json.dumps(data, ensure_ascii=False), version)
                continue
            starts.setdefault(act, set()).add(start)
            chosen[doc_id] = (act, start, json.dumps(data, ensure_ascii=False))
        for act, (doc_id, start, payload, version) in promulgated.items():
            if start in starts.get(act, set()):
                continue
            if act not in selection.full_history and act in starts:
                continue  # an act with consolidated versions: its current one was chosen above
            chosen[doc_id] = (act, start, payload)
        db.executemany(
            "insert or replace into version values (?, ?, ?, ?)",
            [(doc_id, act, start, payload) for doc_id, (act, start, payload) in chosen.items()],
        )
        db.commit()
        return set(chosen)

    def _pass_version_fragments(self, db: sqlite3.Connection, path: Path, doc_ids: set[int]) -> set[int]:
        fids: set[int] = set()
        batch: list[tuple[int, int, int, str]] = []
        seq = 0
        key = '"znění-dokument-id" : '.encode()
        for record in iter_records(gunzip_chunks(path)):
            doc_id = _int_after(record, key)
            if doc_id is None or doc_id not in doc_ids:
                continue
            item = json.loads(record)
            fragment = item.get("právní-akt-fragment") or {}
            fid = fragment.get("fragment-id")
            if fid is None:
                continue
            row = {
                "eli": item.get("znění-fragment-eli"),
                "parent": _eli_path((item.get("znění-fragment-předek") or {}).get("iri")),
                "label_citation": item.get("znění-fragment-citace"),
                "label_number": item.get("znění-fragment-označení-uzlu"),
                "label_text": item.get("znění-fragment-označení-uzlu-text"),
                "url": item.get("znění-fragment-url"),
                "hierarchy": item.get("znění-fragment-hierarchie"),
                "differs_from": item.get("znění-fragment-odlišná-účinnost"),
                "selected_from": item.get("znění-fragment-vybrané-od"),
                "selected_to": item.get("znění-fragment-vybrané-do"),
                "fragment_id": fid,
            }
            seq += 1
            fids.add(fid)
            batch.append((doc_id, seq, fid, json.dumps(row, ensure_ascii=False)))
            if len(batch) >= 5000:
                db.executemany("insert into vfrag values (?, ?, ?, ?)", batch)
                batch.clear()
        db.executemany("insert into vfrag values (?, ?, ?, ?)", batch)
        db.commit()
        return fids

    def _pass_fragments(self, db: sqlite3.Connection, path: Path, fids: set[int]) -> None:
        batch: list[tuple[int, str]] = []
        key = '"fragment-id" : '.encode()
        for record in iter_records(gunzip_chunks(path)):
            fid = _int_after(record, key)
            if fid is None or fid not in fids:
                continue
            item = json.loads(record)
            row = {"type": item.get("cis-esb-typ-fragmentu-položka"), "text": item.get("fragment-text")}
            batch.append((fid, json.dumps(row, ensure_ascii=False)))
            if len(batch) >= 5000:
                db.executemany("insert or replace into ftext values (?, ?)", batch)
                batch.clear()
        db.executemany("insert or replace into ftext values (?, ?)", batch)
        db.commit()

    def _pass_metadata(self, db: sqlite3.Connection, path: Path) -> None:
        wanted: set[int] = set()
        for (data,) in db.execute("select data from version"):
            mid = json.loads(data).get("metadata-id")
            if mid is not None:
                wanted.add(mid)
        key = '"metadata-id" : '.encode()
        batch: list[tuple[int, str]] = []
        for record in iter_records(gunzip_chunks(path)):
            mid = _int_after(record, key)
            if mid is None or mid not in wanted:
                continue
            item = json.loads(record)
            batch.append((mid, json.dumps({k: item.get(k) for k in METADATA_KEYS}, ensure_ascii=False)))
        db.executemany("insert or replace into metadata values (?, ?)", batch)
        db.commit()

    # ------------------------------------------------------------------ documents

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        slice_path = self.build_slice(manifest)
        db = sqlite3.connect(slice_path)
        try:
            files = json.loads(db.execute("select value from meta where key='files'").fetchone()[0])
            generated = max(
                (f.get("retrieved_at") or "" for f in files.values()),
                default="",
            )
            retrieved = datetime.fromisoformat(generated) if generated else datetime.now(UTC)
            order = sorted(
                db.execute("select doc_id, act, start from version").fetchall(),
                key=lambda r: (_act_key(r[1]), r[2], r[0]),
            )
            for doc_id, act, _start in order:
                yield self._document(db, doc_id, act, files, retrieved)
        finally:
            db.close()

    def _document(
        self, db: sqlite3.Connection, doc_id: int, act: str, files: dict[str, Any], retrieved: datetime
    ) -> RawDocument:
        version = json.loads(db.execute("select data from version where doc_id=?", (doc_id,)).fetchone()[0])
        meta_row = db.execute("select data from metadata where mid=?", (version.get("metadata-id"),)).fetchone()
        rows: list[list[Any]] = []
        for (data,) in db.execute("select data from vfrag where doc_id=? order by seq", (doc_id,)):
            row = json.loads(data)
            text = db.execute("select data from ftext where fid=?", (row["fragment_id"],)).fetchone()
            extra = json.loads(text[0]) if text else {"type": None, "text": None}
            row.update(extra)
            rows.append([row.get(c) for c in FRAGMENT_COLUMNS])
        as_of = db.execute("select value from meta where key='as_of'").fetchone()[0]
        document = {
            "format": "esbirka-opendata-slice/1",
            "as_of": as_of,
            "datasets": files,
            "version": version,
            "metadata": json.loads(meta_row[0]) if meta_row else None,
            "columns": list(FRAGMENT_COLUMNS),
            "fragments": rows,
        }
        content = json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        eli = version["znění-eli"]
        return RawDocument(
            external_id=f"esbirka:{eli.removeprefix('/eli/cz/')}",
            uri=f"https://e-sbirka.gov.cz{eli.removeprefix('/eli/cz')}" if eli.startswith("/eli/cz/") else None,
            media_type="application/json",
            content=content,
            retrieved_at=retrieved,
            metadata={"act": act, "document_id": doc_id, "version_eli": eli, "fragments": len(rows)},
        )


def _act_key(act: str) -> tuple[str, int, int]:
    collection, year, number = act.split("/")
    return collection, int(year), int(number)
