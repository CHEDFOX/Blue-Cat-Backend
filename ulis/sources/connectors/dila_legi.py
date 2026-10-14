"""Fetches French consolidated legislation from the DILA "LEGI" open data (Licence Ouverte 2.0).

Source: ``https://echanges.dila.gouv.fr/OPENDATA/LEGI/`` - one *stock* (``Freemium_legi_global_<date>.tar.gz``, the
whole database, about 1.1 GB, 5.5 million files) and one small *incremental* archive per publication day. The
database holds, for every code and every non-codified text (laws, ordinances, decrees, orders), each article **version**
as its own XML file with ``DATE_DEBUT``/``DATE_FIN``/``ETAT``, the sections (``section_ta``) and the structure
(``texte/struct``, ``texte/version``). The manifest (``config/manifests/fr-dila-legi.yaml``) names the stock and
the selection of texts to keep (required identifiers and whole natures, e.g. all codes).

What the connector does (a stream, never the whole dump in memory):

1. **Stock** - downloads the stock once (``dila_dump.DilaArchives``: robots.txt checked, identifying User-Agent,
   resumable, cached) and reads it as a stream; the files of every selected text are written, byte for byte, into one
   zip per text (``<cache>/echanges.dila.gouv.fr/legi-work/<stock>/stock/<text>.zip``). Whether a text is selected
   is decided by its ``texte/version`` file (nature, state, title), or at once when it is a required identifier.
2. **Increments** (optional, ``incrementals`` in the manifest) - the daily archives published after the stock are
   downloaded in order (one request each, at least 5 s apart) and their files for selected texts overlay the stock's
   (``overlay/<text>/<kind>/<file>``); files in the archive's ``liste_suppression_legi.dat`` are removed.
3. **Bundles** - for each selected text one zip = the stock's files overlaid with the increments' files, plus
   ``bundle.json`` (text identifier, tree, last archive applied, currency date). The bundle is the raw document the
   ``dila_legi_xml`` parser reads. Bundles are cached, so a repeated run yields identical bytes.

The identifiers in the archive paths are validated against fixed patterns; no member name is ever used as a file
path. A missing required text is reported in ``fetch_errors`` and the run continues; a refusal by the host
(401/403/429...) stops it.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import zipfile
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from defusedxml.ElementTree import fromstring
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.dila_dump import (
    HOST,
    USER_AGENT,
    Archive,
    DilaArchives,
    archive_from_name,
    iter_tar_files,
)
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError

DATABASE = "LEGI"
BUNDLE_FORMAT = "ulis-dila-legi-bundle/1"
STATE_VERSION = 1
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
# Members waiting for their text's version file are held in memory; anything near this size is not expected.
PENDING_LIMIT = 256 * 1024 * 1024
_PATH = (
    r"legi/global/code_et_TNC_(?:en|non)_vigueur/(?P<tree>[A-Za-z_]+)/(?P<src>JORF|LEGI)/TEXT/"
    r"(?:\d{2}/){5}(?P<tid>(?:JORF|LEGI)TEXT\d{12})/(?P<kind>texte/struct|texte/version|article|section_ta)/"
    r"(?:[A-Za-z0-9_]+/)*(?P<base>[A-Z]{8}\d{12})"
)
MEMBER = re.compile(rf"(?:^|/){_PATH}\.xml$")
DELETED = re.compile(rf"(?:^|/){_PATH}$")
TID = re.compile(r"^(?:JORF|LEGI)TEXT\d{12}$")
NATURE = re.compile(r"^[A-Z_]{2,32}$")

__all__ = ["DilaLegiConnector", "FetchError", "LegiManifest"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StockSpec(_Strict):
    file: str
    date: date
    bytes: int = Field(gt=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class IncrementalSpec(_Strict):
    enabled: bool = False
    # Apply the archives published up to and including this date (None: every archive the host lists).
    through: date | None = None


class RequiredText(_Strict):
    cid: str = Field(pattern=r"^(?:JORF|LEGI)TEXT\d{12}$")
    title: str


class SelectSpec(_Strict):
    required: list[RequiredText] = Field(default_factory=list)
    natures: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class LegiManifest(_Strict):
    schema_version: Literal[1]
    source_id: str
    user_agent: str
    crawl_delay_seconds: float = Field(ge=5.0)
    stock: StockSpec
    incrementals: IncrementalSpec = IncrementalSpec()
    select: SelectSpec


@dataclass
class _Info:
    """Facts read from a text's ``texte/version`` file."""

    cid: str | None
    nature: str | None
    etat: str | None
    title: str | None


@dataclass
class _Pending:
    members: list[tuple[str, str, bytes]] = field(default_factory=list)
    size: int = 0


class _StockWriter:
    """Appends members to one zip per text; only the zip of the current text is open."""

    def __init__(self, directory: Path):
        self.directory = directory
        self._tid: str | None = None
        self._zip: zipfile.ZipFile | None = None

    def write(self, tid: str, kind: str, base: str, data: bytes) -> None:
        if tid != self._tid:
            self.close()
            self.directory.mkdir(parents=True, exist_ok=True)
            self._zip = zipfile.ZipFile(self.directory / f"{tid}.zip", "a", zipfile.ZIP_DEFLATED)
            self._tid = tid
        assert self._zip is not None
        info = zipfile.ZipInfo(f"{kind}/{base}.xml", FIXED_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        self._zip.writestr(info, data)

    def remove(self, tid: str, member: str) -> None:  # the stock is never edited; deletions are applied at build time
        return None

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
        self._zip, self._tid = None, None


class _OverlayWriter:
    """Writes the files of an increment as ``<overlay>/<text>/<kind>/<file>`` (a later archive replaces an earlier)."""

    def __init__(self, directory: Path):
        self.directory = directory

    def write(self, tid: str, kind: str, base: str, data: bytes) -> None:
        target = self.directory / tid / kind
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{base}.xml").write_bytes(data)

    def remove(self, tid: str, member: str) -> None:
        (self.directory / tid / member).unlink(missing_ok=True)

    def close(self) -> None:
        return None


def read_info(content: bytes) -> _Info:
    root = fromstring(content, forbid_dtd=True)

    def text(path: str) -> str | None:
        element = root.find(path)
        return ((element.text or "").strip() or None) if element is not None else None

    return _Info(
        cid=text("META/META_SPEC/META_TEXTE_CHRONICLE/CID"),
        nature=text("META/META_COMMUN/NATURE"),
        etat=text("META/META_SPEC/META_TEXTE_VERSION/ETAT"),
        title=text("META/META_SPEC/META_TEXTE_VERSION/TITREFULL") or text("META/META_SPEC/META_TEXTE_VERSION/TITRE"),
    )


class DilaLegiConnector:
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
        self.archives: DilaArchives | None = None
        self.report: dict[str, Any] = {}

    # ------------------------------------------------------------------ manifest
    def manifest(self) -> LegiManifest:
        try:
            manifest = LegiManifest.model_validate(yaml.safe_load(self.manifest_path.read_text(encoding="utf-8")))
        except (OSError, ValidationError, yaml.YAMLError) as exc:
            raise ManifestError(f"{self.manifest_path}: {exc}") from exc
        if manifest.source_id != self.expected_source_id:
            raise ManifestError("manifest source_id mismatch")
        if manifest.user_agent != USER_AGENT:
            raise ManifestError(f"user_agent must be {USER_AGENT!r}")
        stock = archive_from_name(manifest.stock.file, DATABASE)
        if stock is None or stock.kind != "stock":
            raise ManifestError(f"unexpected stock file name {manifest.stock.file!r}")
        if stock.date != manifest.stock.date:
            raise ManifestError("stock.date does not match the stock file name")
        if not manifest.select.required and not manifest.select.natures:
            raise ManifestError("the manifest selects nothing")
        for nature in manifest.select.natures:
            if not NATURE.match(nature):
                raise ManifestError(f"invalid nature {nature!r}")
        for cid in manifest.select.exclude:
            if not TID.match(cid):
                raise ManifestError(f"invalid excluded identifier {cid!r}")
        listed = [t.cid for t in manifest.select.required]
        if len(set(listed)) != len(listed):
            raise ManifestError("a required text is listed twice")
        return manifest

    # ------------------------------------------------------------------ layout and state
    def _work(self, manifest: LegiManifest) -> Path:
        stamp = manifest.stock.file.removeprefix("Freemium_legi_global_").removesuffix(".tar.gz")
        return self.cache_dir / HOST / "legi-work" / stamp

    @staticmethod
    def _selection_hash(manifest: LegiManifest) -> str:
        payload = json.dumps(
            {"stock": manifest.stock.file, "select": manifest.select.model_dump(mode="json"), "v": STATE_VERSION},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _load_state(self, work: Path, manifest: LegiManifest) -> dict[str, Any]:
        path = work / "state.json"
        if path.exists():
            state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            if state.get("selection") == self._selection_hash(manifest) and state.get("version") == STATE_VERSION:
                return state
            shutil.rmtree(work)  # a different selection: start again from the cached archives
        return {
            "version": STATE_VERSION,
            "selection": self._selection_hash(manifest),
            "stock_done": False,
            "applied": [],
            "texts": {},
            "deleted": {},
            "stats": {},
            "built_for": None,
        }

    @staticmethod
    def _save_state(work: Path, state: dict[str, Any]) -> None:
        work.mkdir(parents=True, exist_ok=True)
        tmp = work / "state.json.tmp"
        tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        tmp.replace(work / "state.json")

    # ------------------------------------------------------------------ selection
    @staticmethod
    def _wanted(manifest: LegiManifest, tid: str, info: _Info) -> bool:
        exclude = set(manifest.select.exclude)
        if tid in exclude or (info.cid in exclude):
            return False
        if tid in {t.cid for t in manifest.select.required}:
            return True
        return info.nature in set(manifest.select.natures)

    @staticmethod
    def _remember(entry: dict[str, Any], version_xml: bytes, tree: str) -> None:
        info = read_info(version_xml)
        entry.update(cid=info.cid, nature=info.nature, etat=info.etat, title=info.title, tree=tree)

    # ------------------------------------------------------------------ one archive
    def _scan(
        self,
        path: Path,
        manifest: LegiManifest,
        state: dict[str, Any],
        sink: _StockWriter | _OverlayWriter,
        archive_name: str,
    ) -> dict[str, Any]:
        """Stream one archive: route the files of selected texts to ``sink``; return counters."""
        required = {t.cid for t in manifest.select.required}
        texts: dict[str, dict[str, Any]] = state["texts"]
        decided: dict[str, bool] = {tid: True for tid in texts}  # selected by an earlier archive
        pending: dict[str, _Pending] = {}
        pending_size = 0
        natures: Counter[str] = Counter()
        written: set[tuple[str, str]] = set()
        deletions: list[str] = []
        counts: dict[str, Any] = {"members": 0, "written": 0, "texts_selected": 0, "texts_seen": 0}

        def keep(tid: str, kind: str, base: str, data: bytes) -> bool:
            """Store a file. A file that appears twice in one archive (a text left in both the in-force and the
            not-in-force tree, with different states) is stored once: the first copy wins."""
            short = kind.removeprefix("texte/")
            member = f"{short}/{base}.xml"
            if (tid, member) in written:
                counts["duplicates_ignored"] = counts.get("duplicates_ignored", 0) + 1
                return False
            sink.write(tid, short, base, data)
            written.add((tid, member))
            counts["written"] += 1
            return True

        def select(tid: str, tree: str) -> None:
            texts.setdefault(tid, {"cid": None, "nature": None, "etat": None, "title": None, "tree": tree})
            decided[tid] = True
            counts["texts_selected"] += 1
            for p_kind, p_base, p_data in pending.pop(tid, _Pending()).members:
                keep(tid, p_kind, p_base, p_data)

        for name, _size, read in iter_tar_files(path):
            if name.endswith("liste_suppression_legi.dat"):
                deletions = read().decode("utf-8", "replace").split()
                continue
            match = MEMBER.search(name)
            if match is None:
                continue
            counts["members"] += 1
            tid, kind, base, tree = match["tid"], match["kind"], match["base"], match["tree"]
            if decided.get(tid) is None and tid in required:
                bucket = pending.get(tid)
                pending_size -= bucket.size if bucket else 0
                select(tid, tree)
            verdict = decided.get(tid)
            if verdict is False:
                continue
            data = read()
            if verdict:
                if keep(tid, kind, base, data):
                    texts[tid]["tree"] = tree
                    if kind == "texte/version":
                        self._remember(texts[tid], data, tree)
                continue
            if kind != "texte/version":  # undecided: wait for the text's version file
                bucket = pending.setdefault(tid, _Pending())
                bucket.members.append((kind, base, data))
                bucket.size += len(data)
                pending_size += len(data)
                if pending_size > PENDING_LIMIT:
                    raise FetchError(f"{archive_name}: too many bytes before the version file of {tid}")
                continue
            counts["texts_seen"] += 1
            info = read_info(data)
            natures[f"{info.nature}/{info.etat}"] += 1
            bucket = pending.get(tid)
            pending_size -= bucket.size if bucket else 0
            if self._wanted(manifest, tid, info):
                select(tid, tree)
                keep(tid, kind, base, data)
                self._remember(texts[tid], data, tree)
            else:
                decided[tid] = False
                pending.pop(tid, None)
        # Files the archive deletes, unless it supplies them again (as when a text changes tree).
        deleted: dict[str, list[str]] = state["deleted"]
        for line in deletions:
            hit = DELETED.search(line.strip())
            if hit is None or hit["tid"] not in texts:
                continue
            member = f"{hit['kind'].removeprefix('texte/')}/{hit['base']}.xml"
            if (hit["tid"], member) in written:
                continue
            sink.remove(hit["tid"], member)
            if member not in deleted.setdefault(hit["tid"], []):
                deleted[hit["tid"]].append(member)
        for tid, member in written:
            if member in deleted.get(tid, []):
                deleted[tid].remove(member)
        counts["unresolved_texts"] = len(pending)
        stats = state["stats"].setdefault("natures", {})
        for key, n in natures.items():
            stats[key] = stats.get(key, 0) + n
        return counts

    # ------------------------------------------------------------------ the run
    def fetch(self) -> Iterator[RawDocument]:
        manifest = self.manifest()
        work = self._work(manifest)
        archives = DilaArchives(
            DATABASE,
            self.cache_dir,
            offline=self.offline,
            crawl_delay=manifest.crawl_delay_seconds,
            sleep=self._sleep,
        )
        self.archives = archives
        archives.check_robots()
        state = self._load_state(work, manifest)
        stock = archive_from_name(manifest.stock.file, DATABASE)
        assert stock is not None
        stock_path = archives.ensure(manifest.stock.file, manifest.stock.bytes)
        if not state["stock_done"]:
            if manifest.stock.sha256 and _sha256_file(stock_path) != manifest.stock.sha256:
                raise FetchError(f"{stock_path}: SHA-256 differs from the manifest")
            shutil.rmtree(work / "stock", ignore_errors=True)
            state["texts"], state["deleted"], state["applied"], state["stats"] = {}, {}, [], {}
            writer = _StockWriter(work / "stock")
            try:
                counts = self._scan(stock_path, manifest, state, writer, manifest.stock.file)
            finally:
                writer.close()
            state["stats"]["stock"] = counts
            state["stock_done"] = True
            self._save_state(work, state)
        for required in manifest.select.required:
            if required.cid not in state["texts"]:
                self.fetch_errors.append(f"{required.cid} ({required.title}) is not in the stock")
        for increment in self._increments(archives, manifest, stock, state):
            if increment.name in state["applied"]:
                continue
            counts = self._scan(
                archives.ensure(increment.name), manifest, state, _OverlayWriter(work / "overlay"), increment.name
            )
            state["applied"].append(increment.name)
            state["stats"].setdefault("increments", {})[increment.name] = counts
            self._save_state(work, state)
        yield from self._bundles(work, manifest, state, stock)

    def _increments(
        self, archives: DilaArchives, manifest: LegiManifest, stock: Archive, state: dict[str, Any]
    ) -> list[Archive]:
        if not manifest.incrementals.enabled:
            return []
        wanted = {
            a.name: a
            for a in archives.listing()
            if a.kind == "increment"
            and a.stamp > stock.stamp
            and (manifest.incrementals.through is None or a.date <= manifest.incrementals.through)
        }
        for name in state["applied"]:  # applied archives stay applied (offline runs have no listing)
            known = archive_from_name(name, DATABASE)
            if known is not None:
                wanted.setdefault(name, known)
        return sorted(wanted.values(), key=lambda a: a.stamp)

    def _bundles(
        self, work: Path, manifest: LegiManifest, state: dict[str, Any], stock: Archive
    ) -> Iterator[RawDocument]:
        applied = [a for a in (archive_from_name(n, DATABASE) for n in state["applied"]) if a is not None]
        last = max(applied, key=lambda a: a.stamp) if applied else stock
        last_dir = last.date
        built_dir = work / "bundles"
        if state.get("built_for") != last.name:
            shutil.rmtree(built_dir, ignore_errors=True)
            state["built_for"] = last.name
            self._save_state(work, state)
        built_dir.mkdir(parents=True, exist_ok=True)
        required_order = {t.cid: i for i, t in enumerate(manifest.select.required)}
        texts: dict[str, dict[str, Any]] = state["texts"]
        order = sorted(
            texts,
            key=lambda t: (required_order.get(t, len(required_order)), texts[t].get("nature") or "", t),
        )
        stock_url = f"https://{HOST}/OPENDATA/{DATABASE}/{manifest.stock.file}"
        self.report = {
            "texts": len(order),
            "currency_date": last_dir.isoformat(),
            "increments_applied": len(applied),
            "natures": dict(Counter(f"{texts[t]['nature']}" for t in order)),
        }
        last_path = self.archives.path(last.name) if self.archives else None
        retrieved = datetime.fromtimestamp(
            last_path.stat().st_mtime if last_path is not None and last_path.exists() else time.time(), UTC
        )
        for tid in order:
            target = built_dir / f"{tid}.zip"
            if not target.exists():
                try:
                    self._build_bundle(work, manifest, state, tid, target, last, len(applied))
                except FetchError as exc:
                    self.fetch_errors.append(str(exc))
                    continue
            info = texts[tid]
            yield RawDocument(
                external_id=tid,
                uri=f"{stock_url}#{tid}",
                media_type="application/zip",
                content=target.read_bytes(),
                retrieved_at=retrieved,
                metadata={
                    "database": DATABASE,
                    "text": tid,
                    "nature": info.get("nature"),
                    "title": info.get("title"),
                    "tree": info.get("tree"),
                    "stock": {"name": manifest.stock.file, "date": manifest.stock.date.isoformat(), "url": stock_url},
                    "last_archive": {"name": last.name, "date": last_dir.isoformat()},
                    "increments_applied": len(applied),
                },
            )

    def _build_bundle(
        self,
        work: Path,
        manifest: LegiManifest,
        state: dict[str, Any],
        tid: str,
        target: Path,
        last: Archive,
        n_increments: int,
    ) -> None:
        stock_zip = work / "stock" / f"{tid}.zip"
        overlay = work / "overlay" / tid
        deleted = set(state["deleted"].get(tid, []))
        members: dict[str, Path | None] = {}  # member name -> overlay file (None: the stock's file)
        if stock_zip.exists():
            with zipfile.ZipFile(stock_zip) as zf:
                members.update({n: None for n in zf.namelist() if n not in deleted})
        for file in overlay.glob("*/*.xml") if overlay.exists() else []:
            name = f"{file.parent.name}/{file.name}"
            if name not in deleted:
                members[name] = file
        if not any(n.startswith("version/") for n in members):
            raise FetchError(f"{tid}: the archives hold no version file for this text; not ingested")
        info = state["texts"][tid]
        meta = {
            "format": BUNDLE_FORMAT,
            "database": DATABASE,
            "text": tid,
            "tree": info.get("tree"),
            "stock": {"name": manifest.stock.file, "date": manifest.stock.date.isoformat()},
            "increments_applied": n_increments,
            "last_archive": {"name": last.name, "date": last.date.isoformat()},
            "currency_date": last.date.isoformat(),
        }
        tmp = target.with_suffix(".zip.tmp")
        stock_handle = zipfile.ZipFile(stock_zip) if stock_zip.exists() else None
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
                for name in sorted([*members, "bundle.json"]):
                    entry = zipfile.ZipInfo(name, FIXED_TIME)
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    if name == "bundle.json":
                        out.writestr(entry, json.dumps(meta, sort_keys=True, ensure_ascii=False).encode("utf-8"))
                        continue
                    source = members[name]
                    if source is not None:
                        out.writestr(entry, source.read_bytes())
                    else:
                        assert stock_handle is not None
                        out.writestr(entry, stock_handle.read(name))
        finally:
            if stock_handle is not None:
                stock_handle.close()
        tmp.replace(target)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
