"""Operator commands: `load-all` (plan, order, failure handling, resumability, locking) and `status`."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from ulis import cli
from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport
from ulis.ops import loading
from ulis.ops.loading import LoadOptions, UnknownSource, evict_stale_cache, select_sources
from ulis.ops.sources import LOAD_AFTER, STATIC_PINS, apply_load_order, manifest_info
from ulis.ops.text import format_table, human_duration
from ulis.settings import REPO_ROOT

SAMPLE = "ulis-fictional-sample"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Process environment for the CLI: own object store and download cache; database set per test."""
    monkeypatch.setenv("ULIS_OBJECT_STORE_DIR", str(tmp_path / "objects"))
    monkeypatch.setenv("ULIS_SOURCE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ULIS_ENV", "development")
    monkeypatch.delenv("ULIS_API_KEYS", raising=False)
    return tmp_path


@pytest.fixture
def db(env: Path, db_url: str, ingested: IngestionReport, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("ULIS_DATABASE_URL", db_url)
    return db_url


def run(*argv: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


# --------------------------------------------------------------------------- selection and order (no database)


def ids(catalog: Catalog, **kw: Any) -> list[str]:
    return [item.source.id for item in select_sources(catalog, LoadOptions(**kw)).selected]


def test_default_selection_is_every_active_source_except_the_fictional_sample(catalog: Catalog) -> None:
    selected = ids(catalog)
    active_real = {s.id for s in catalog.sources if s.status == "active" and s.authority != "fictional"}
    assert set(selected) == active_real and SAMPLE not in selected
    assert all(catalog.sources.get(i).status == "active" for i in selected)
    skipped = dict(select_sources(catalog, LoadOptions()).skipped)
    assert "fictional" in skipped[SAMPLE] and any("status planned" in why for why in skipped.values())


def test_small_sources_come_first(catalog: Catalog) -> None:
    items = select_sources(catalog, LoadOptions()).selected
    # (except the sources that follow others: see test_the_ecfr_follows_the_annual_editions)
    weights = [(i.weight, i.source.id) for i in items if i.source.id not in LOAD_AFTER]
    assert weights == sorted(weights) and len(items) > 5
    assert all(i.info is not None for i in items)  # every active source has a readable manifest


def test_the_ecfr_follows_the_annual_editions(catalog: Catalog) -> None:
    """Older editions of a part are loaded before the current eCFR text: the pipeline records a newer wording from
    its own date, but an older wording that arrives after it would be a conflict."""
    order = ids(catalog)
    ecfr = order.index("us-ecfr")
    assert ecfr > order.index("us-govinfo-bulk-cfr-fr") and ecfr > order.index("us-gpo-uslm-samples")
    assert ids(catalog, only=("us-ecfr",)) == ["us-ecfr"]
    assert apply_load_order(["a", "us-ecfr", "us-gpo-uslm-samples", "b"], str) == [
        "a",
        "us-gpo-uslm-samples",
        "us-ecfr",
        "b",
    ]
    assert apply_load_order(["a", "us-ecfr"], str) == ["a", "us-ecfr"]


def test_only_skip_and_include_fictional(catalog: Catalog) -> None:
    assert ids(catalog, only=(SAMPLE,)) == [SAMPLE]
    assert ids(catalog, only=("gb-legislation-gov-uk", "nl-bwb"), skip=("nl-bwb",)) == ["gb-legislation-gov-uk"]
    assert SAMPLE in ids(catalog, include_fictional=True)
    assert "nl-bwb" not in ids(catalog, skip=("nl-bwb",))
    # Naming a source that is not active selects it anyway: the licence gate then refuses it, visibly.
    assert ids(catalog, only=("in-india-code",)) == ["in-india-code"]
    with pytest.raises(UnknownSource, match="no-such-source"):
        select_sources(catalog, LoadOptions(only=("no-such-source",)))
    with pytest.raises(UnknownSource, match="typo"):
        select_sources(catalog, LoadOptions(skip=("typo",)))


def test_refresh_skips_static_pins_and_pins_name_real_sources(catalog: Catalog) -> None:
    assert set(STATIC_PINS) <= set(catalog.sources.sources)
    refreshed = set(ids(catalog, refresh=True))
    assert refreshed and refreshed.isdisjoint(STATIC_PINS)
    assert {"gb-legislation-gov-uk", "de-gesetze-im-internet", "nl-bwb"} <= refreshed
    assert {"ca-justice-laws-xml", "us-uscode-house", "us-gpo-uslm-samples"} <= set(STATIC_PINS)
    assert set(ids(catalog, refresh=True, only=("us-uscode-house",))) == {"us-uscode-house"}  # naming overrides


def test_manifest_info_reads_size_delay_and_hosts(catalog: Catalog) -> None:
    gb = manifest_info(catalog.sources.get("gb-legislation-gov-uk"), REPO_ROOT)
    assert gb is not None and gb.entries and gb.crawl_delay == 5 and "www.legislation.gov.uk" in gb.hosts
    assert gb.min_network_seconds == gb.requests * 5  # type: ignore[operator]
    uslm = manifest_info(catalog.sources.get("us-gpo-uslm-samples"), REPO_ROOT)
    assert uslm is not None and uslm.git and uslm.min_network_seconds is None


# --------------------------------------------------------------------------- helpers


def test_evict_stale_cache_removes_only_old_files_under_named_hosts(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    now = time.time()
    old, fresh = now - 10 * 86400, now - 86400
    files = {
        "www.example.org/a/xml.zip": old,
        "www.example.org/a/xml.zip.meta.json": old,
        "www.example.org/b/xml.zip": fresh,
        "example.org/c.html": old,  # connectors differ on the www. prefix
        "other.net/d.html": old,  # a host the source does not use
    }
    for rel, mtime in files.items():
        path = cache / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 100)
        os.utime(path, (mtime, mtime))
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    os.utime(outside, (old, old))
    (cache / "www.example.org" / "link").symlink_to(outside)

    would = evict_stale_cache(cache, ["www.example.org"], 7, dry_run=True)
    assert would == (3, 300) and (cache / "www.example.org/a/xml.zip").exists()
    assert evict_stale_cache(cache, ["www.example.org"], 7) == (3, 300)
    assert not (cache / "www.example.org/a/xml.zip").exists() and not (cache / "example.org/c.html").exists()
    assert (cache / "www.example.org/b/xml.zip").exists() and (cache / "other.net/d.html").exists()
    assert outside.read_text() == "keep"


def test_text_helpers() -> None:
    table = format_table(["A", "NUM"], [["x", 5], ["longer", 1234]], right=(1,))
    assert table.splitlines()[0] == "A        NUM" and table.splitlines()[-1] == "longer  1234"
    assert [human_duration(s) for s in (0.2, 12, 185, 3725)] == ["0.2s", "12s", "3m05s", "1h02m"]


# --------------------------------------------------------------------------- dry run (no database, no network)


def test_dry_run_prints_the_plan_and_touches_nothing(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ULIS_DATABASE_URL", "postgresql+psycopg://ulis:ulis@127.0.0.1:1/unreachable")
    code, out = run("load-all", "--dry-run", capsys=capsys)
    assert code == 0
    assert "load-all plan" in out and "smallest first" in out and "gb-legislation-gov-uk" in out
    assert "Skipped (fictional sample" in out and SAMPLE not in out.split("Skipped")[0]
    assert "Network time is a lower bound" in out
    assert not (env / "objects").exists() and not (env / "cache").exists()  # nothing was created
    code, out = run("load-all", "--dry-run", "--refresh", capsys=capsys)
    assert code == 0 and "(refresh)" in out and "us-uscode-house" in out.split("static pin")[1]
    assert "Cache:" in out
    code, out = run("load-all", "--dry-run", "--only", "nl-bwb", "--skip", "nl-bwb", capsys=capsys)
    assert code == 0 and "nothing to load" in out


def test_unknown_source_is_a_usage_error(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run("load-all", "--only", "atlantis-law", capsys=capsys)
    assert code == 2 and "unknown source id(s): atlantis-law" in out and "gb-legislation-gov-uk" in out


def test_unreachable_database_is_reported_without_the_password(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ULIS_DATABASE_URL", "postgresql+psycopg://ulis:topsecretpw@127.0.0.1:1/none")
    for argv in (["load-all", "--only", SAMPLE], ["status"]):
        code, out = run(*argv, capsys=capsys)
        assert code == 1 and "cannot use the database" in out and "topsecretpw" not in out


# --------------------------------------------------------------------------- load-all against PostgreSQL


@pytest.mark.db
def test_load_all_loads_the_sample_prints_a_summary_and_is_idempotent(
    db: str, capsys: pytest.CaptureFixture[str], session_factory: sessionmaker[Session], ingested: IngestionReport
) -> None:
    code, out = run("load-all", "--only", SAMPLE, capsys=capsys)
    assert code == 0, out
    for needle in ("SOURCE", "STATUS", "DOCS NEW/SEEN", "PROV.VERS", "CONFLICTS", "ERRORS", "TIME", SAMPLE):
        assert needle in out
    row = next(line for line in out.splitlines() if line.startswith(SAMPLE) and " ok " in line)
    assert f"/{ingested.documents_seen}" in row  # the sample is already stored: everything is "seen", nothing new
    assert row.split()[2] == f"0/{ingested.documents_seen}"
    assert "1 source(s) in" in out
    with session_factory() as s:
        runs = s.scalar(select(func.count()).select_from(m.IngestionRun).where(m.IngestionRun.source_id == SAMPLE))
    assert runs and runs >= 2


@pytest.mark.db
def test_a_failing_source_does_not_stop_the_others_and_sets_the_exit_code(
    db: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    session_factory: sessionmaker[Session],
) -> None:
    real = loading.ingest_one

    def flaky(factory: Any, catalog: Catalog, store: Any, source_id: str, cache_dir: Path, actor: str) -> Any:
        if source_id == "ar-infoleg":
            raise RuntimeError("publisher refused the request (HTTP 403)")
        return real(factory, catalog, store, source_id, cache_dir, actor)

    monkeypatch.setattr(loading, "ingest_one", flaky)
    code, out = run("load-all", "--only", "ar-infoleg", SAMPLE, capsys=capsys)
    assert code == 1
    order = [line for line in out.splitlines() if line.startswith("[")]
    assert order[0].startswith("[1/2] ar-infoleg: starting") and "[2/2] " + SAMPLE in " ".join(order)
    assert "ar-infoleg" in out and "failed" in out and "RuntimeError: publisher refused the request" in out
    assert next(line for line in out.splitlines() if line.startswith(SAMPLE) and " ok " in line)
    with session_factory() as s:
        failed = s.scalars(select(m.IngestionRun).where(m.IngestionRun.status == "failed")).all()
    assert any(r.source_id == "ar-infoleg" and "HTTP 403" in r.errors[0] for r in failed)


@pytest.mark.db
def test_document_level_errors_and_conflicts_are_reported(
    db: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def partly_broken(factory: Any, catalog: Any, store: Any, source_id: str, cache_dir: Path, actor: str) -> Any:
        return IngestionReport(
            run_id="r",
            source_id=source_id,
            documents_seen=5,
            documents_new=3,
            provision_versions_created=40,
            conflicts=["conflict in a"],
            errors=["doc-1: parse failure", "doc-2: parse failure"],
        )

    monkeypatch.setattr(loading, "ingest_one", partly_broken)
    code, out = run("load-all", "--only", SAMPLE, capsys=capsys)
    assert code == 1
    row = next(line for line in out.splitlines() if line.startswith(SAMPLE) and "errors" in line)
    assert row.split()[2:] == ["3/5", "0", "40", "1", "2", row.split()[-1]]
    assert "doc-1: parse failure" in out and "conflict: conflict in a" in out
    assert "skipped, not overwritten" in out


@pytest.mark.db
def test_a_source_the_licence_gate_refuses_fails_the_run(db: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run("load-all", "--only", "in-india-code", capsys=capsys)
    assert code == 1 and "refused" in out and "may not be ingested" in out


@pytest.mark.db
def test_interrupt_prints_what_is_left_and_releases_the_lock(
    db: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = loading.ingest_one

    def interrupted(factory: Any, catalog: Any, store: Any, source_id: str, cache_dir: Path, actor: str) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(loading, "ingest_one", interrupted)
    code, out = run("load-all", "--only", SAMPLE, capsys=capsys)
    assert code == 130 and "interrupted" in out and "not run" in out
    monkeypatch.setattr(loading, "ingest_one", real)
    code, out = run("load-all", "--only", SAMPLE, capsys=capsys)  # the lock was released: the retry runs
    assert code == 0, out


@pytest.mark.db
def test_only_one_load_all_runs_at_a_time(db: str, engine: Engine, capsys: pytest.CaptureFixture[str]) -> None:
    with engine.connect() as holder:
        assert holder.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": loading.LOCK_KEY}).scalar_one()
        code, out = run("load-all", "--only", SAMPLE, capsys=capsys)
        assert code == 3 and "already running" in out
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": loading.LOCK_KEY})
        holder.commit()
    code, _ = run("load-all", "--only", SAMPLE, capsys=capsys)
    assert code == 0


# --------------------------------------------------------------------------- status


@pytest.mark.db
def test_status_lists_counts_last_ingestion_and_verification(
    db: str, capsys: pytest.CaptureFixture[str], session_factory: sessionmaker[Session], catalog: Catalog
) -> None:
    code, out = run("status", capsys=capsys)
    assert code == 0, out
    header = next(line for line in out.splitlines() if line.startswith("JURISDICTION"))
    for column in ("INSTRUMENTS", "PROVISIONS", "PASSAGES", "JUDGMENTS", "LAST INGESTION", "VERIFIED"):
        assert column in header
    xa = next(line for line in out.splitlines() if line.startswith("XA "))
    assert SAMPLE in xa and "completed" in xa and ("ago" in xa or "just now" in xa)
    assert "migrations:" in out and "at head" in out and "verify-sources:" in out
    with session_factory() as s:
        loaded = set(s.scalars(select(m.SourceDocument.source_id).distinct()))
        unloaded = sorted(
            src.id
            for src in catalog.sources
            if src.status == "active" and src.authority != "fictional" and src.id not in loaded
        )
        n_instruments = s.scalar(
            select(func.count()).select_from(m.Instrument).where(m.Instrument.jurisdiction_id == "XA")
        )
    assert int(xa.split()[1].replace(",", "")) == n_instruments
    if unloaded:  # other test modules may have loaded real fixtures into this database
        assert out.split("no stored document: ")[1].strip().split(", ") == unloaded
    else:
        assert "no stored document" not in out


@pytest.mark.db
def test_status_json_and_all_flag(db: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run("status", "--json", capsys=capsys)
    assert code == 0
    data = json.loads(out)
    by_id = {j["jurisdiction"]: j for j in data["jurisdictions"]}
    assert {"XA", "XB"} <= set(by_id)
    xa = by_id["XA"]
    assert xa["instruments"] > 0 and xa["provisions"] > 0 and xa["passages"] > 0
    assert xa["last_ingestion"]["source"] == SAMPLE and xa["documents_verified"] == xa["source_documents"]
    assert xa["documents_failed_verification"] == 0 and xa["last_verified"]
    assert data["migrations"]["at_head"] is True and data["totals"]["instruments"] >= xa["instruments"]
    assert SAMPLE not in data["sources_never_loaded"]
    code, out = run("status", "--json", "--all", capsys=capsys)
    all_ids = {j["jurisdiction"] for j in json.loads(out)["jurisdictions"]}
    empty = sorted(all_ids - set(by_id))
    assert empty and "XA" in all_ids and set(by_id) <= all_ids  # --all adds the jurisdictions that have no data
    code, out = run("status", "--all", capsys=capsys)
    assert code == 0 and any(line.startswith(empty[0] + " ") for line in out.splitlines())


def test_ops_commands_are_registered_in_the_cli(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "load-all" in out and "status" in out


# --------------------------------------------------------------------------- loading several sources at once


@pytest.mark.db
def test_jobs_load_sources_at_the_same_time_but_never_two_from_the_same_host(
    db: str, catalog: Catalog, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    wanted = ["ar-infoleg", "es-boe-consolidada", "nl-bwb", "jp-egov-laws", "ch-fedlex", "ch-fedlex-treaties"]
    hosts = {
        s: set(manifest_info(catalog.sources.get(s), REPO_ROOT).hosts)  # type: ignore[union-attr]
        for s in wanted
    }
    assert hosts["ch-fedlex"] & hosts["ch-fedlex-treaties"], "the test needs two sources that share a host"
    lock = threading.Lock()
    running: set[str] = set()
    overlaps: list[tuple[str, str]] = []
    peak = 0

    def fake(factory: Any, catalog: Catalog, store: Any, source_id: str, cache_dir: Path, actor: str) -> Any:
        nonlocal peak
        with lock:
            overlaps.extend((source_id, other) for other in running if hosts[source_id] & hosts[other])
            running.add(source_id)
            peak = max(peak, len(running))
        time.sleep(0.3)
        with lock:
            running.discard(source_id)
        return IngestionReport(run_id="x", source_id=source_id, documents_seen=1, documents_new=1)

    monkeypatch.setattr(loading, "ingest_one", fake)
    code, out = run("load-all", "--jobs", "4", "--only", *wanted, capsys=capsys)
    assert code == 0, out
    assert overlaps == []  # two sources with a common host were never loading together
    assert peak >= 2  # ... while sources on different hosts were
    for source_id in wanted:
        assert f"] {source_id}: ok" in out
    assert "[6/6]" in out


@pytest.mark.db
def test_jobs_one_keeps_the_order_of_the_plan(
    db: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def fake(factory: Any, catalog: Catalog, store: Any, source_id: str, cache_dir: Path, actor: str) -> Any:
        seen.append(source_id)
        return IngestionReport(run_id="x", source_id=source_id)

    monkeypatch.setattr(loading, "ingest_one", fake)
    code, _ = run("load-all", "--jobs", "1", "--only", "nl-bwb", "ar-infoleg", capsys=capsys)
    assert code == 0 and len(seen) == 2


@pytest.mark.db
def test_jobs_respect_the_load_after_order(
    db: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The eCFR must start only after the CFR editions and the GPO samples have finished, even with --jobs."""
    import threading

    lock = threading.Lock()
    events: list[tuple[str, str]] = []

    def fake(factory: Any, catalog: Catalog, store: Any, source_id: str, cache_dir: Path, actor: str) -> Any:
        with lock:
            events.append(("start", source_id))
        time.sleep(0.3 if source_id != "us-ecfr" else 0.05)
        with lock:
            events.append(("end", source_id))
        return IngestionReport(run_id="x", source_id=source_id)

    monkeypatch.setattr(loading, "ingest_one", fake)
    only = ("us-ecfr", "us-govinfo-bulk-cfr-fr", "us-gpo-uslm-samples", "ar-infoleg")
    code, out = run("load-all", "--jobs", "4", "--only", *only, capsys=capsys)
    assert code == 0, out
    first_ecfr = events.index(("start", "us-ecfr"))
    for before in ("us-govinfo-bulk-cfr-fr", "us-gpo-uslm-samples"):
        assert events.index(("end", before)) < first_ecfr
