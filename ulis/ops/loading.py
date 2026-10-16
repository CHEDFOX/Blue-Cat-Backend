"""`load-all`: ingest every active source on a server, small sources first, resumable, tolerant of failures.

Resumability comes from two existing properties: connectors cache every download under
``ULIS_SOURCE_CACHE_DIR`` (a re-run does not fetch again) and ingestion is idempotent (documents are
keyed by SHA-256, so a re-run adds only what is new).  Each source is ingested in its own transaction:
one failing source rolls back alone and the run continues with the next.
"""

from __future__ import annotations

import signal
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import make_session_factory, session_scope
from ulis.db import models as m
from ulis.ingestion.catalog_sync import sync_catalog
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.ops import sources as ops_sources
from ulis.ops.sources import DEFAULT_ENTRIES, STATIC_PINS, ManifestInfo
from ulis.ops.text import format_table, human_bytes, human_duration
from ulis.settings import REPO_ROOT, Settings
from ulis.sources.registry import LicenceNotApproved, SourceDescriptor, licence_gate
from ulis.storage.object_store import LocalObjectStore, ObjectStore

# Session-level PostgreSQL advisory lock: only one load-all runs against a database at a time.
LOCK_KEY = 0x554C4953_4C4F4144
EXIT_OK, EXIT_ERRORS, EXIT_USAGE, EXIT_LOCKED, EXIT_INTERRUPTED = 0, 1, 2, 3, 130
MAX_MESSAGES = 3
# Hosts that descriptors cite (licence texts, standards, the project) but sources are not fetched from.
NOT_FETCHED_FROM = ("creativecommons.org", "github.com", "w3.org", "iso.org", "wikipedia.org", "opendefinition.org")


class UnknownSource(ValueError):
    pass


class AlreadyRunning(RuntimeError):
    pass


@dataclass(frozen=True)
class LoadOptions:
    only: tuple[str, ...] = ()
    skip: tuple[str, ...] = ()
    dry_run: bool = False
    # Weekly maintenance mode: only "live" sources, and cached downloads older than max_cache_age_days
    # are discarded first so that the publisher's current text is fetched again.
    refresh: bool = False
    max_cache_age_days: float = 7.0
    # The fictional sample is invented law; it is only loaded when asked for.
    include_fictional: bool = False
    # Also load the sources marked `load_by_default: false` (old editions, historical enactments).
    include_history: bool = False
    # Court decisions (sources that hold nothing but judgments) are not loaded by default: the laws are the product.
    include_case_law: bool = False
    # Also load the sources of countries outside the launch markets (config/launch-markets.yaml).
    all_markets: bool = False
    actor: str = "load-all"
    # Sources loaded at the same time. Never two on the same host (so the crawl delay each publisher asked for
    # holds), whatever this is set to; 1 = one source after the other.
    jobs: int = 1


@dataclass(frozen=True)
class PlanItem:
    source: SourceDescriptor
    info: ManifestInfo | None

    @property
    def weight(self) -> int:
        return self.info.weight if self.info else DEFAULT_ENTRIES


@dataclass
class Selection:
    selected: list[PlanItem] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (source id, reason)


@dataclass
class SourceResult:
    source_id: str
    status: str  # ok | errors | failed | refused | not run
    documents_seen: int = 0
    documents_new: int = 0
    instrument_versions: int = 0
    provision_versions: int = 0
    conflicts: int = 0
    errors: int = 0
    seconds: float = 0.0
    messages: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.status in ("failed", "errors", "refused")


def launch_markets(base_dir: Path = REPO_ROOT) -> frozenset[str]:
    """The jurisdictions in ``config/launch-markets.yaml``; empty (no restriction) when the file does not exist."""
    path = base_dir / "config" / "launch-markets.yaml"
    if not path.is_file():
        return frozenset()
    data = yaml.safe_load(path.read_text("utf-8")) or {}
    return frozenset(str(item) for item in data.get("markets") or [])


def in_markets(catalog: Catalog, source: SourceDescriptor, markets: frozenset[str]) -> bool:
    """Whether the source covers a market, or a state, province or region below one."""
    for jurisdiction in source.jurisdictions:
        if jurisdiction in markets:
            return True
        if jurisdiction in catalog.jurisdictions and markets & set(catalog.jurisdictions.ancestors(jurisdiction)):
            return True
    return False


def is_case_law(source: SourceDescriptor) -> bool:
    """A source of court decisions only (a source that also holds laws or treaties is not)."""
    return set(source.document_kinds) == {"judgment"}


def select_sources(catalog: Catalog, options: LoadOptions, base_dir: Path = REPO_ROOT) -> Selection:
    """Choose and order the sources to load. Raises :class:`UnknownSource` for ids that do not exist."""
    known = catalog.sources.sources
    unknown = sorted((set(options.only) | set(options.skip)) - set(known))
    if unknown:
        raise UnknownSource(f"unknown source id(s): {', '.join(unknown)}. Known: {', '.join(sorted(known))}")
    selection = Selection()
    markets = frozenset() if options.all_markets else launch_markets(base_dir)
    for source in known.values():
        named = source.id in options.only
        if options.only and not named:
            continue
        reason = None
        if source.id in options.skip:
            reason = "--skip"
        elif not named:  # naming a source explicitly overrides the defaults below (the licence gate still applies)
            if source.status != "active":
                reason = f"status {source.status}"
            elif source.authority == "fictional" and not options.include_fictional:
                reason = "fictional sample: only with --include-fictional or --only"
            elif is_case_law(source) and not options.include_case_law:
                reason = "court decisions, not loaded by default: only with --include-case-law or --only"
            elif markets and source.authority != "fictional" and not in_markets(catalog, source, markets):
                reason = f"outside the launch markets ({', '.join(sorted(markets))}): only with --all-markets or --only"
            elif not source.load_by_default and not options.include_history:
                reason = "history / old editions, not the law in force today: only with --include-history or --only"
            elif options.refresh and source.id in STATIC_PINS:
                reason = "static pin: nothing new until its manifest changes"
        if reason:
            selection.skipped.append((source.id, reason))
        else:
            selection.selected.append(PlanItem(source, ops_sources.manifest_info(source, base_dir)))
    selection.selected.sort(key=lambda item: (item.weight, item.source.id))  # small first
    selection.selected = ops_sources.apply_load_order(selection.selected, lambda item: item.source.id)
    selection.skipped.sort()
    return selection


def evict_stale_cache(
    cache_dir: Path, hosts: Iterable[str], max_age_days: float, *, dry_run: bool = False, now: float | None = None
) -> tuple[int, int]:
    """Delete cached downloads older than ``max_age_days`` below ``<cache_dir>/<host>``.

    Connectors cache by host name (some without the ``www.`` prefix), so both spellings are tried.
    Returns (files, bytes) deleted - or that would be deleted when ``dry_run``.
    """
    root = cache_dir.resolve()
    cutoff = (time.time() if now is None else now) - max_age_days * 86400
    names: set[str] = set()
    for host in hosts:
        bare = host.removeprefix("www.")
        names.update({host, bare, "www." + bare})
    files = size = 0
    for name in sorted(names):
        base = root / name
        if base.is_symlink() or not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            if stat.st_mtime < cutoff:
                files += 1
                size += stat.st_size
                if not dry_run:
                    path.unlink(missing_ok=True)
    return files, size


def format_plan(selection: Selection, options: LoadOptions, cache_dir: Path | None = None) -> str:
    rows: list[list[object]] = []
    total_seconds = 0.0
    for n, item in enumerate(selection.selected, 1):
        info = item.info
        floor = info.min_network_seconds if info else None
        total_seconds += floor or 0.0
        rows.append(
            [
                n,
                item.source.id,
                "static" if item.source.id in STATIC_PINS else "live",
                info.entries if info and info.entries is not None else "?",
                f"{info.requests:,}" if info and info.requests is not None else "?",
                "git clone" if info and info.git else (f"{info.crawl_delay:g}s" if info and info.crawl_delay else "-"),
                f">= {human_duration(floor)}" if floor else "-",
            ]
        )
    lines = [
        f"load-all plan{' (refresh)' if options.refresh else ''}: {len(selection.selected)} source(s), smallest first",
        format_table(["#", "SOURCE", "KIND", "ENTRIES", "REQUESTS>=", "DELAY", "NETWORK TIME"], rows, right=(0, 3, 4)),
    ]
    if total_seconds:
        lines.append(
            f"Network time is a lower bound (requests x crawl delay; sources that fetch version histories need more): "
            f">= {human_duration(total_seconds)} in total; parsing and indexing come on top."
        )
    if options.refresh and cache_dir is not None:
        files = size = 0
        for item in selection.selected:
            if item.info:
                f, b = evict_stale_cache(cache_dir, item.info.hosts, options.max_cache_age_days, dry_run=True)
                files, size = files + f, size + b
        lines.append(
            f"Cache: {files} cached download(s), {human_bytes(size)}, older than {options.max_cache_age_days:g} days "
            f"under {cache_dir} would be discarded and fetched again."
        )
    by_reason: dict[str, list[str]] = {}
    for sid, why in selection.skipped:
        by_reason.setdefault(why, []).append(sid)
    for why, ids in sorted(by_reason.items()):
        lines.append(f"Skipped ({why}): {', '.join(ids)}")
    return "\n".join(lines)


def format_summary(results: list[SourceResult], elapsed: float) -> str:
    rows = [
        [
            r.source_id,
            r.status,
            f"{r.documents_new}/{r.documents_seen}",
            r.instrument_versions,
            r.provision_versions,
            r.conflicts,
            r.errors,
            human_duration(r.seconds),
        ]
        for r in results
    ]
    table = format_table(
        ["SOURCE", "STATUS", "DOCS NEW/SEEN", "INSTR.VERS", "PROV.VERS", "CONFLICTS", "ERRORS", "TIME"],
        rows,
        right=(2, 3, 4, 5, 6, 7),
    )
    lines = [table, "", f"{len(results)} source(s) in {human_duration(elapsed)}"]
    bad = [r for r in results if r.messages]
    for r in bad:
        lines.append(f"{r.source_id}:")
        lines.extend(f"  - {msg}" for msg in r.messages[:MAX_MESSAGES])
        if len(r.messages) > MAX_MESSAGES:
            lines.append(f"  - ... {len(r.messages) - MAX_MESSAGES} more (see ingestion_runs.errors)")
    if any(r.conflicts for r in results):
        lines.append(
            "Conflicts are texts that differ from an already-recorded version: they were skipped, not overwritten."
        )
    return "\n".join(lines)


def ingest_one(
    factory: sessionmaker[Session], catalog: Catalog, store: ObjectStore, source_id: str, cache_dir: Path, actor: str
) -> IngestionReport:
    """Ingest one source in its own transaction (committed on success, rolled back on error)."""
    with session_scope(factory) as session:
        return Ingestor(session, catalog, store, cache_dir=cache_dir).ingest_source(source_id, actor=actor)


def _record_failed_run(factory: sessionmaker[Session], source_id: str, actor: str, message: str) -> None:
    """So that `status` shows a source whose run crashed (its own transaction was rolled back)."""
    try:
        with session_scope(factory) as session:
            session.add(
                m.IngestionRun(
                    source_id=source_id,
                    actor=actor,
                    status="failed",
                    finished_at=datetime.now(UTC),
                    errors=[message[:2000]],
                )
            )
    except Exception:  # noqa: S110 - best effort; the failure is already reported
        pass


@contextmanager
def single_instance(engine: Engine) -> Iterator[None]:
    with engine.connect() as conn:
        got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar_one()
        conn.commit()  # do not sit "idle in transaction" for hours
        if not got:
            raise AlreadyRunning("another load-all is already running against this database")
        try:
            yield
        finally:
            conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            conn.commit()


@contextmanager
def _sigterm_as_interrupt() -> Iterator[None]:
    """`docker stop` / systemd send SIGTERM: stop between steps and still print what was done."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def handler(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def run_load_all(
    settings: Settings,
    catalog: Catalog,
    engine: Engine,
    options: LoadOptions,
    out: Callable[[str], None] = lambda line: print(line, flush=True),
) -> int:
    """Run the plan. Returns the process exit code (0 ok, 1 any source errored, 3 already running, 130 interrupted)."""
    try:
        selection = select_sources(catalog, options)
    except UnknownSource as exc:
        out(f"error: {exc}")
        return EXIT_USAGE
    if not selection.selected:
        out(
            "nothing to load"
            + (f" (skipped: {', '.join(s for s, _ in selection.skipped)})" if selection.skipped else "")
        )
        return EXIT_OK
    if options.dry_run:
        out(format_plan(selection, options, settings.source_cache_dir))
        return EXIT_OK

    factory = make_session_factory(engine)
    store = LocalObjectStore(settings.object_store_dir)
    results: list[SourceResult] = []
    started = time.monotonic()
    explicit = set(options.only)
    try:
        with single_instance(engine), _sigterm_as_interrupt():
            with session_scope(factory) as session:
                out(f"catalog synchronised: {sync_catalog(session, catalog, actor=options.actor)}")
            out(format_plan(selection, options))
            if options.jobs > 1:
                _run_parallel(selection, options, settings, catalog, factory, store, explicit, results, out)
            else:
                _run_sequential(selection, options, settings, catalog, factory, store, explicit, results, out)
    except AlreadyRunning as exc:
        out(f"error: {exc}")
        return EXIT_LOCKED
    except KeyboardInterrupt:
        done = {r.source_id for r in results}
        results.extend(SourceResult(i.source.id, "not run") for i in selection.selected if i.source.id not in done)
        out("interrupted - the work already done is kept and downloads are cached; run the same command to resume\n")
        out(format_summary(results, time.monotonic() - started))
        return EXIT_INTERRUPTED
    out("")
    out(format_summary(results, time.monotonic() - started))
    return EXIT_ERRORS if any(r.failed for r in results) else EXIT_OK


def _host_keys(item: PlanItem) -> frozenset[str]:
    """What two sources must not share while loading at the same time: the publisher domains named in their
    manifest and in their descriptor (a connector may also fetch from a host only its code names, so this is a
    conservative approximation: it errs on the side of loading fewer things at once)."""
    hosts: set[str] = set(item.info.hosts) if item.info else set()
    hosts |= {h for h in ops_sources.url_hosts(item.source.model_dump_json()) if not h.endswith(NOT_FETCHED_FROM)}
    keys = {ops_sources.registrable_domain(h) for h in hosts}
    return frozenset(keys or {f"source:{item.source.id}"})


def _announce_and_load(
    label: str,
    item: PlanItem,
    options: LoadOptions,
    settings: Settings,
    catalog: Catalog,
    factory: sessionmaker[Session],
    store: ObjectStore,
    explicit: set[str],
    out: Callable[[str], None],
) -> SourceResult:
    if options.refresh and item.info:
        files, size = evict_stale_cache(settings.source_cache_dir, item.info.hosts, options.max_cache_age_days)
        if files:
            out(f"{label}: discarded {files} stale cached download(s) ({human_bytes(size)})")
    entries = item.info.entries if item.info else "?"
    out(f"{label}: starting ({entries} manifest entries)")
    result = _load_one(factory, catalog, store, settings, item.source, options, explicit)
    out(
        f"{label}: {result.status} - {result.documents_new} new of {result.documents_seen} documents, "
        f"{result.errors} errors, {human_duration(result.seconds)}"
    )
    return result


def _run_sequential(
    selection: Selection,
    options: LoadOptions,
    settings: Settings,
    catalog: Catalog,
    factory: sessionmaker[Session],
    store: ObjectStore,
    explicit: set[str],
    results: list[SourceResult],
    out: Callable[[str], None],
) -> None:
    total = len(selection.selected)
    for n, item in enumerate(selection.selected, 1):
        label = f"[{n}/{total}] {item.source.id}"
        results.append(_announce_and_load(label, item, options, settings, catalog, factory, store, explicit, out))


def _run_parallel(
    selection: Selection,
    options: LoadOptions,
    settings: Settings,
    catalog: Catalog,
    factory: sessionmaker[Session],
    store: ObjectStore,
    explicit: set[str],
    results: list[SourceResult],
    out: Callable[[str], None],
) -> None:
    """Load up to ``options.jobs`` sources at once, in plan order, never two that share a host.

    Each source still runs in its own transaction. Worker threads are daemons: on Ctrl-C/SIGTERM the workers
    still running are abandoned, their transactions end with the process (rolled back), downloads stay cached.
    """
    pending = list(selection.selected)
    total = len(pending)
    busy: set[str] = set()
    running: set[str] = set()
    cond = threading.Condition()
    stop = threading.Event()
    started = 0
    out_lock = threading.Lock()

    def say(line: str) -> None:
        with out_lock:
            out(line)

    def take() -> tuple[PlanItem, frozenset[str]] | None:  # cond must be held
        waiting = {p.source.id for p in pending} | running
        for i, item in enumerate(pending):
            keys = _host_keys(item)
            # a source that must load after others (LOAD_AFTER) waits until those have finished
            if any(p in waiting for p in ops_sources.LOAD_AFTER.get(item.source.id, ())):
                continue
            if busy.isdisjoint(keys):
                pending.pop(i)
                busy.update(keys)
                running.add(item.source.id)
                return item, keys
        return None

    def worker() -> None:
        nonlocal started
        while True:
            with cond:
                while True:
                    if stop.is_set() or not pending:
                        return
                    picked = take()
                    if picked is not None:
                        break
                    cond.wait(timeout=1.0)  # every remaining source shares a host with a running one
                started += 1
                label = f"[{started}/{total}] {picked[0].source.id}"
            item, keys = picked
            result = _announce_and_load(label, item, options, settings, catalog, factory, store, explicit, say)
            with cond:
                busy.difference_update(keys)
                running.discard(item.source.id)
                results.append(result)
                cond.notify_all()

    threads = [
        threading.Thread(target=worker, name=f"load-all-{n}", daemon=True) for n in range(min(options.jobs, total))
    ]
    for thread in threads:
        thread.start()
    try:
        while any(thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(timeout=0.5)
    except KeyboardInterrupt:
        stop.set()
        raise


def _load_one(
    factory: sessionmaker[Session],
    catalog: Catalog,
    store: ObjectStore,
    settings: Settings,
    source: SourceDescriptor,
    options: LoadOptions,
    explicit: set[str],
) -> SourceResult:
    began = time.monotonic()
    result = SourceResult(source.id, "ok")
    try:
        try:
            licence_gate(source)
        except LicenceNotApproved as exc:
            # Active sources that fail the gate are a catalog problem; fail the run only if the operator asked for it.
            result.status = "refused" if source.id in explicit else "skipped"
            result.messages.append(str(exc))
            return result
        report = ingest_one(factory, catalog, store, source.id, settings.source_cache_dir, options.actor)
        result.documents_seen = report.documents_seen
        result.documents_new = report.documents_new
        result.instrument_versions = report.instrument_versions_created
        result.provision_versions = report.provision_versions_created
        result.conflicts = len(report.conflicts)
        result.errors = len(report.errors)
        result.messages = [*report.errors, *(f"conflict: {c}" for c in report.conflicts)]
        result.status = "errors" if report.errors else "ok"
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # a failing source must not stop the others
        result.status = "failed"
        result.errors += 1
        result.messages.append(f"{type(exc).__name__}: {exc}")
        _record_failed_run(factory, source.id, options.actor, f"{type(exc).__name__}: {exc}")
    finally:
        result.seconds = time.monotonic() - began
    return result
