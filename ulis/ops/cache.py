"""`prune-cache`: delete the download cache of the sources whose stored documents all passed `verify-sources`.

Connectors keep every download under ``ULIS_SOURCE_CACHE_DIR`` (``<host>/...``, git clones as ``<owner>__<repo>``) so
that a load can resume without fetching again. Once a source's documents are in the object store (the exact bytes,
content-addressed) and every one of them has been re-derived from those bytes by `verify-sources` with a pass, the
cached downloads are a second copy of the same bytes and can go. Nothing else is touched:

* a source is **prunable** only when it has stored documents, every one of them has a `verify-sources` result (a
  re-derivation, not the hash check ingestion records itself) and the latest one passed, and its latest ingestion run
  completed without errors;
* the cache is organised by host, not by source, so a cache directory is deleted only when every source that names
  its host (in its manifest, the same host list `load-all --refresh` evicts by) and has been loaded at all (it has
  stored documents or an ingestion run) is prunable. A source that failed, errored or is not (fully) verified keeps
  its host's directory whole. Sources that were never loaded do not hold a directory back (they have no downloads
  of their own to lose; whatever they would need is fetched when they are loaded);
* ``robots.txt`` (and its ``.meta.json``) stays in each host directory: `load-all --dry-run` reads the Crawl-delay
  from it; it is a few hundred bytes;
* directories of hosts no manifest names (a connector's code-only host) are never touched.

A later run of a pruned source downloads what it needs again: see docs/deployment.md, "Least disk space, no loss".
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import make_session_factory, session_scope
from ulis.ops import sources as ops_sources
from ulis.ops.loading import NOT_FETCHED_FROM, AlreadyRunning, cache_names, single_instance
from ulis.ops.text import format_table, human_bytes
from ulis.settings import REPO_ROOT, Settings
from ulis.storage.object_store import ObjectStore, open_object_store
from ulis.verification.integrity import verify_source_document

EXIT_OK, EXIT_ERRORS, EXIT_USAGE, EXIT_LOCKED = 0, 1, 2, 3
# Kept in each host directory: the Crawl-delay `load-all --dry-run` shows comes from here.
KEEP_FILES = frozenset({"robots.txt", "robots.txt.meta.json"})
# The hash check ingestion records when it stores a document is not a re-derivation from the stored bytes.
PIPELINE_CHECKER = "pipeline"
_REPOSITORY = re.compile(r"(?m)^repository:\s*['\"]?(https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)['\"]?\s*$")


@dataclass(frozen=True)
class SourceState:
    source_id: str
    documents: int = 0
    verified: int = 0  # documents with a verify-sources result
    failed: int = 0  # documents whose latest verify-sources result is not "pass"
    last_run: str | None = None  # status of the latest ingestion run

    @property
    def loaded(self) -> bool:
        return self.documents > 0 or self.last_run is not None

    @property
    def problem(self) -> str | None:
        """Why the source's downloads must stay; None when they may go."""
        if self.documents == 0:
            return "no stored documents" + (f" (last run: {self.last_run})" if self.last_run else "")
        if self.last_run not in (None, "completed"):
            return f"last ingestion run: {self.last_run}"
        if self.failed:
            return f"{self.failed} document(s) failed verify-sources"
        if self.verified < self.documents:
            return f"{self.documents - self.verified} of {self.documents} document(s) not verified yet (verify-sources)"
        return None


@dataclass
class CacheDir:
    name: str  # directory below the cache root
    path: Path
    sources: tuple[str, ...]  # the sources that name it (loaded ones only)
    git: bool = False
    files: int = 0
    size: int = 0
    blocked_by: list[str] = field(default_factory=list)  # "source: reason"


@dataclass
class PrunePlan:
    cache_dir: Path
    states: dict[str, SourceState]
    considered: list[str]  # the sources asked about, in order
    prune: list[CacheDir] = field(default_factory=list)
    keep: list[CacheDir] = field(default_factory=list)

    @property
    def files(self) -> int:
        return sum(d.files for d in self.prune)

    @property
    def size(self) -> int:
        return sum(d.size for d in self.prune)


def source_cache_names(source_id: str, catalog: Catalog, base_dir: Path = REPO_ROOT) -> tuple[set[str], set[str]]:
    """(host directory names, git clone directory names) a source's downloads are cached under: the hosts its manifest
    names, as `load-all --refresh` evicts them (``evict_stale_cache``), or the clone of a git snapshot."""
    source = catalog.sources.get(source_id)
    info = ops_sources.manifest_info(source, base_dir)
    if info is None:
        return set(), set()
    if info.git:
        endpoint = source.access.endpoint or ""
        try:
            raw = (base_dir / endpoint).read_text(encoding="utf-8")
        except OSError:
            return set(), set()
        found = _REPOSITORY.search(raw)
        if not found:
            return set(), set()
        return set(), {found.group(1).removeprefix("https://github.com/").replace("/", "__")}
    return cache_names(h for h in info.hosts if not h.endswith(NOT_FETCHED_FROM)), set()


def source_states(session: Session) -> dict[str, SourceState]:
    """Documents, verify-sources results and the latest ingestion run of every source that has any."""
    rows = session.execute(
        text(
            "SELECT d.source_id, count(*), count(v.result), "
            "count(*) FILTER (WHERE v.result IS NOT NULL AND v.result <> 'pass') "
            "FROM source_documents d LEFT JOIN ("
            "  SELECT DISTINCT ON (subject_id) subject_id, result FROM verification_records "
            "  WHERE subject_kind = 'source_document' AND \"check\" = 'source_integrity' AND checker <> :pipeline "
            "  ORDER BY subject_id, checked_at DESC"
            ") v ON v.subject_id = d.id::text GROUP BY d.source_id"
        ),
        {"pipeline": PIPELINE_CHECKER},
    )
    states = {sid: SourceState(sid, int(n), int(ver), int(bad)) for sid, n, ver, bad in rows}
    runs = session.execute(
        text("SELECT DISTINCT ON (source_id) source_id, status FROM ingestion_runs ORDER BY source_id, started_at DESC")
    )
    for sid, status in runs:
        old = states.get(sid, SourceState(sid))
        states[sid] = SourceState(sid, old.documents, old.verified, old.failed, status)
    return states


def _measure(path: Path, *, git: bool) -> tuple[int, int]:
    """(files, bytes) that pruning ``path`` deletes. Symbolic links are never followed nor counted."""
    files = size = 0
    for root, dirs, names in os.walk(path, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for name in names:
            full = os.path.join(root, name)
            if os.path.islink(full) or (not git and root == str(path) and name in KEEP_FILES):
                continue
            try:
                files += 1
                size += os.stat(full).st_size
            except OSError:
                continue
    return files, size


def plan_prune(
    session: Session, catalog: Catalog, cache_dir: Path, only: Iterable[str] = (), base_dir: Path = REPO_ROOT
) -> PrunePlan:
    """Which cache directories go and which stay (and why), from the database's current state."""
    return build_plan(source_states(session), catalog, cache_dir, only, base_dir)


def build_plan(
    states: dict[str, SourceState],
    catalog: Catalog,
    cache_dir: Path,
    only: Iterable[str] = (),
    base_dir: Path = REPO_ROOT,
) -> PrunePlan:
    """``only`` limits the sources whose downloads are pruned (every source still counts when deciding whether a
    shared host directory may go)."""
    root = cache_dir.resolve()
    only = list(only)
    considered = only or sorted(catalog.sources.sources)
    # Every cache directory name -> the sources that name it.
    names: dict[str, set[str]] = {}
    claims: dict[str, set[str]] = {}
    git_names: set[str] = set()
    for sid in catalog.sources.sources:
        hosts, clones = source_cache_names(sid, catalog, base_dir)
        names[sid] = hosts | clones
        for name in names[sid]:
            claims.setdefault(name, set()).add(sid)
        git_names |= clones
    plan = PrunePlan(cache_dir=root, states=states, considered=considered)
    wanted: set[str] = set()
    for sid in considered:
        if states.get(sid, SourceState(sid)).problem is None:
            wanted |= names.get(sid, set())
    for name in sorted(wanted):
        path = root / name
        if path.is_symlink() or not path.is_dir() or path.resolve().parent != root:
            continue
        holders = sorted(s for s in claims.get(name, ()) if states.get(s, SourceState(s)).loaded)
        entry = CacheDir(name, path, tuple(holders), git=name in git_names)
        entry.files, entry.size = _measure(path, git=entry.git)
        for sid in holders:
            problem = states[sid].problem
            if problem is not None:
                entry.blocked_by.append(f"{sid}: {problem}")
        (plan.keep if entry.blocked_by else plan.prune).append(entry)
    return plan


def apply_prune(plan: PrunePlan) -> tuple[int, int]:
    """Delete what the plan prunes. Returns (files, bytes) deleted."""
    files = size = 0
    for entry in plan.prune:
        path = entry.path
        if path.is_symlink() or not path.is_dir() or path.resolve().parent != plan.cache_dir:
            continue
        if entry.git:
            f, b = _measure(path, git=True)
            shutil.rmtree(path)
            files, size = files + f, size + b
            continue
        for root, dirs, names in os.walk(path, topdown=False, followlinks=False):
            for name in names:
                full = os.path.join(root, name)
                if (root == str(path) and name in KEEP_FILES) or os.path.islink(full):
                    continue  # symbolic links are left alone (and never followed)
                try:
                    st = os.lstat(full)
                    os.unlink(full)
                except OSError:
                    continue
                files += 1
                size += st.st_size
            for name in dirs:
                full = os.path.join(root, name)
                if os.path.islink(full):
                    continue
                with contextlib.suppress(OSError):
                    os.rmdir(full)  # only empty directories; anything left in one keeps it
    return files, size


def format_prune(plan: PrunePlan, *, dry_run: bool, freed: tuple[int, int] | None = None) -> str:
    """The report; ``freed`` is (files, bytes) actually deleted (the plan's figures when not given)."""
    rows = []
    for sid in plan.considered:
        st = plan.states.get(sid)
        if st is None or not st.loaded:
            continue
        rows.append([sid, st.documents, st.verified, st.failed, st.last_run or "-", st.problem or "prunable"])
    lines = [
        format_table(["SOURCE", "DOCS", "VERIFIED", "FAILED", "LAST RUN", "DOWNLOADS"], rows, right=(1, 2, 3))
        if rows
        else "no loaded source among those asked about",
        "",
    ]
    dirs = [[d.name, "delete", d.files, human_bytes(d.size)] for d in plan.prune]
    dirs += [[d.name, "keep", d.files, human_bytes(d.size)] for d in plan.keep]
    if dirs:
        lines.append(format_table(["CACHE DIRECTORY", "ACTION", "FILES", "SIZE"], dirs, right=(2, 3)))
    for d in plan.keep:
        lines.append(f"kept {d.name}: shared with " + "; ".join(d.blocked_by))
    files, size = freed if freed is not None else (plan.files, plan.size)
    verb = "would free" if dry_run else "freed"
    lines.append(
        f"prune-cache {verb} {human_bytes(size)} in {files:,} file(s) under {plan.cache_dir}"
        + (" (dry run: nothing deleted)" if dry_run else "")
    )
    return "\n".join(lines)


def unverified_documents(session: Session, source_ids: Iterable[str]) -> list[tuple[str, uuid.UUID]]:
    """(source id, document id) of the documents of these sources that have no verify-sources result yet."""
    ids = list(source_ids)
    if not ids:
        return []
    rows = session.execute(
        text(
            "SELECT d.source_id, d.id FROM source_documents d WHERE d.source_id = ANY(:ids) AND NOT EXISTS ("
            "  SELECT 1 FROM verification_records v WHERE v.subject_id = d.id::text "
            "  AND v.subject_kind = 'source_document' AND v.\"check\" = 'source_integrity' AND v.checker <> :pipeline"
            ") ORDER BY d.source_id, d.id"
        ),
        {"ids": ids, "pipeline": PIPELINE_CHECKER},
    )
    return [(sid, uuid.UUID(str(doc))) for sid, doc in rows]


def verify_unverified(
    factory: sessionmaker[Session],
    catalog: Catalog,
    store: ObjectStore,
    source_ids: Iterable[str],
    checker: str,
    out: Callable[[str], None],
) -> int:
    """Run the verify-sources check on every document of these sources that has none yet (one transaction per
    source). Returns the number of documents that failed."""
    with session_scope(factory) as session:
        todo = unverified_documents(session, source_ids)
    by_source: dict[str, list[uuid.UUID]] = {}
    for sid, doc in todo:
        by_source.setdefault(sid, []).append(doc)
    failures = 0
    for sid, docs in by_source.items():
        bad = 0
        with session_scope(factory) as session:
            for doc in docs:
                bad += verify_source_document(session, catalog, store, doc, checker=checker)["result"] != "pass"
        out(f"verify-sources {sid}: {len(docs) - bad} of {len(docs)} new document(s) passed")
        failures += bad
    return failures


def prune(
    factory: sessionmaker[Session],
    catalog: Catalog,
    cache_dir: Path,
    *,
    only: Iterable[str] = (),
    dry_run: bool = False,
    out: Callable[[str], None] = print,
    base_dir: Path = REPO_ROOT,
) -> PrunePlan:
    with session_scope(factory) as session:
        plan = plan_prune(session, catalog, cache_dir, only, base_dir)
    freed = None if dry_run else apply_prune(plan)
    out(format_prune(plan, dry_run=dry_run, freed=freed))
    return plan


def run_prune_cache(
    settings: Settings,
    catalog: Catalog,
    engine: Engine,
    *,
    only: Iterable[str] = (),
    dry_run: bool = False,
    out: Callable[[str], None] = lambda line: print(line, flush=True),
) -> int:
    """The `prune-cache` command. Holds the `load-all` lock, so it never deletes downloads a load is using."""
    only = list(only)
    unknown = sorted(set(only) - set(catalog.sources.sources))
    if unknown:
        out(f"error: unknown source id(s): {', '.join(unknown)}")
        return EXIT_USAGE
    factory = make_session_factory(engine)
    try:
        with single_instance(engine):
            prune(factory, catalog, settings.source_cache_dir, only=only, dry_run=dry_run, out=out)
    except AlreadyRunning as exc:
        out(f"error: {exc} (prune-cache waits for it to finish)")
        return EXIT_LOCKED
    return EXIT_OK


def prune_after_load(
    settings: Settings,
    catalog: Catalog,
    factory: sessionmaker[Session],
    source_ids: list[str],
    actor: str,
    out: Callable[[str], None],
) -> int:
    """`load-all --prune-cache`, called with the load-all lock held after a load in which no source failed: verify the
    new documents of the loaded sources, then prune the downloads of those that are fully verified."""
    if not source_ids:
        return EXIT_OK
    failures = verify_unverified(factory, catalog, open_object_store(settings), source_ids, actor, out)
    prune(factory, catalog, settings.source_cache_dir, only=source_ids, out=out)
    if failures:
        out(f"{failures} document(s) failed verify-sources: their sources' downloads were kept")
        return EXIT_ERRORS
    return EXIT_OK
