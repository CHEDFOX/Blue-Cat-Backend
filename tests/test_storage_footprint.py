"""Least disk space without loss: the zstd object store, `compact-objects` and `prune-cache`."""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path
from typing import Any

import pytest
import zstandard
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from ulis import cli
from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport
from ulis.ops import cache, loading
from ulis.ops.cache import SourceState, build_plan
from ulis.settings import DEFAULT_OBJECT_COMPRESSION_LEVEL, REPO_ROOT, load_settings
from ulis.storage.object_store import IntegrityError, LocalObjectStore
from ulis.verification.integrity import verify_source_document

SAMPLE = "ulis-fictional-sample"
XML = (REPO_ROOT / "sample_corpus/xa/act-2015-12.eng@2023-03-01.xml").read_bytes()
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def key_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def plain_path(root: Path, key: str) -> Path:
    digest = key.removeprefix("sha256:")
    return root / "sha256" / digest[:2] / digest[2:4] / digest


def zst_path(root: Path, key: str) -> Path:
    path = plain_path(root, key)
    return path.with_name(path.name + ".zst")


def write_legacy(root: Path, data: bytes) -> str:
    """An object as stores wrote them before compression: the plain bytes under <digest>."""
    key = key_of(data)
    path = plain_path(root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, 0o600)
    return key


def run(*argv: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


# --------------------------------------------------------------------------- object store


def test_put_keeps_the_key_of_the_original_bytes_and_stores_them_compressed(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    assert store.compression_level == DEFAULT_OBJECT_COMPRESSION_LEVEL
    key = store.put(XML)
    assert key == key_of(XML)  # the address is the SHA-256 of what the publisher sent, never of the .zst
    on_disk = zst_path(tmp_path, key)
    assert on_disk.exists() and not plain_path(tmp_path, key).exists()
    assert on_disk.read_bytes()[:4] == ZSTD_MAGIC and on_disk.stat().st_size < len(XML) / 2
    assert oct(on_disk.stat().st_mode & 0o777) == "0o600"
    assert store.get(key) == XML  # byte for byte
    assert store.exists(key) and store.put(XML) == key  # idempotent: nothing rewritten
    assert [p.name for p in on_disk.parent.iterdir()] == [on_disk.name]  # no temporary file left behind
    assert list(store.iter_keys()) == [key]
    empty = store.put(b"")
    assert store.get(empty) == b""


def test_legacy_uncompressed_objects_are_read_and_not_duplicated(tmp_path: Path) -> None:
    key = write_legacy(tmp_path, XML)
    store = LocalObjectStore(tmp_path)
    assert store.exists(key) and store.get(key) == XML
    assert store.put(XML) == key
    assert not zst_path(tmp_path, key).exists()  # one copy only
    assert store.locate(key) == plain_path(tmp_path, key)


def test_level_zero_writes_plain_files_and_reads_compressed_ones(tmp_path: Path) -> None:
    compressed_key = LocalObjectStore(tmp_path, compression_level=19).put(b"written compressed " * 100)
    plain = LocalObjectStore(tmp_path, compression_level=0)
    key = plain.put(XML)
    assert plain_path(tmp_path, key).read_bytes() == XML and not zst_path(tmp_path, key).exists()
    assert plain.get(compressed_key) == b"written compressed " * 100
    for bad in (-1, 23):
        with pytest.raises(ValueError):
            LocalObjectStore(tmp_path, compression_level=bad)


def test_a_corrupted_or_substituted_compressed_object_is_an_integrity_error(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    key = store.put(XML)
    path = zst_path(tmp_path, key)
    blob = bytearray(path.read_bytes())
    blob[len(blob) // 2] ^= 0xFF  # one flipped bit in the middle of the frame
    path.write_bytes(bytes(blob))
    with pytest.raises(IntegrityError):
        store.get(key)
    path.write_bytes(b"not zstd at all")
    with pytest.raises(IntegrityError):
        store.get(key)
    # A valid frame holding other bytes: the SHA-256 of the decompressed bytes catches it.
    path.write_bytes(zstandard.ZstdCompressor().compress(XML.replace(b"five years", b"four years")))
    with pytest.raises(IntegrityError, match="does not match its digest"):
        store.get(key)
    with pytest.raises(FileNotFoundError):
        store.get(key_of(b"never stored"))
    with pytest.raises(ValueError):
        store.get("sha256:../../etc/passwd")


def test_the_compression_level_setting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ULIS_OBJECT_COMPRESSION_LEVEL", raising=False)
    assert load_settings().object_compression_level == DEFAULT_OBJECT_COMPRESSION_LEVEL
    monkeypatch.setenv("ULIS_OBJECT_COMPRESSION_LEVEL", "0")
    assert load_settings().object_compression_level == 0
    monkeypatch.setenv("ULIS_OBJECT_COMPRESSION_LEVEL", "19")
    settings = load_settings()
    assert settings.object_compression_level == 19
    from ulis.storage.object_store import open_object_store

    monkeypatch.setenv("ULIS_OBJECT_STORE_DIR", str(tmp_path / "objects"))
    assert open_object_store(load_settings()).compression_level == 19
    for bad in ("23", "-1", "fast"):
        monkeypatch.setenv("ULIS_OBJECT_COMPRESSION_LEVEL", bad)
        with pytest.raises(ValueError, match="ULIS_OBJECT_COMPRESSION_LEVEL"):
            load_settings()


# --------------------------------------------------------------------------- compact-objects


def test_compact_converts_legacy_objects_safely_and_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    docs = [XML, XML.replace(b"five", b"six"), b"<a>" + b"x" * 5000 + b"</a>"]
    keys = [write_legacy(root, d) for d in docs]
    damaged = write_legacy(root, b"<doc>original</doc>")
    plain_path(root, damaged).write_bytes(b"<doc>tampered</doc>")  # must be reported, never "repaired"
    # An interrupted earlier run: the compressed copy was written, the plain one not yet removed.
    both = write_legacy(root, b"<b>" + b"y" * 3000 + b"</b>")
    zst_path(root, both).write_bytes(zstandard.ZstdCompressor(level=3).compress(b"<b>" + b"y" * 3000 + b"</b>"))
    stale = plain_path(root, keys[0]).parent / f".{keys[0][7:]}.zst.abc.tmp"
    stale.write_bytes(b"half-written")
    os.utime(stale, (time.time() - 3 * 86400,) * 2)
    store = LocalObjectStore(root, compression_level=9)
    before = sum(p.stat().st_size for p in (root / "sha256").rglob("*") if p.is_file() and p != stale)

    report = store.compact(9, jobs=2)
    assert report.objects == 5 and report.converted == 3 and report.duplicates_removed == 1
    assert report.stale_tmp_removed == 1 and not stale.exists()
    assert len(report.errors) == 1 and damaged in report.errors[0]
    assert report.bytes_before == before and report.bytes_after < before
    for key, data in zip(keys, docs, strict=True):
        assert not plain_path(root, key).exists() and zst_path(root, key).exists()
        assert store.get(key) == data
    assert plain_path(root, damaged).read_bytes() == b"<doc>tampered</doc>"  # untouched, still detected
    with pytest.raises(IntegrityError):
        store.get(damaged)
    assert not plain_path(root, both).exists() and store.get(both) == b"<b>" + b"y" * 3000 + b"</b>"

    again = store.compact(9)
    assert again.converted == 0 and again.unchanged == 4 and again.bytes_after == again.bytes_before
    recompressed = store.compact(19, recompress=True)
    assert recompressed.bytes_after <= recompressed.bytes_before
    assert recompressed.recompressed + recompressed.unchanged == 4
    for key, data in zip(keys, docs, strict=True):
        assert store.get(key) == data
    with pytest.raises(ValueError):
        store.compact(0)


def test_compact_objects_command_reports_bytes_before_and_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "objects"
    keys = [write_legacy(root, XML), write_legacy(root, XML + b"<!-- second -->")]
    monkeypatch.setenv("ULIS_OBJECT_STORE_DIR", str(root))
    monkeypatch.setenv("ULIS_OBJECT_COMPRESSION_LEVEL", "0")  # compaction still compresses (default level)
    code, out = run("compact-objects", capsys=capsys)
    assert code == 0, out
    assert f"zstd level {DEFAULT_OBJECT_COMPRESSION_LEVEL}" in out and "2 compressed" in out
    assert "bytes before" in out and "after" in out and "saved" in out
    assert all(zst_path(root, k).exists() and not plain_path(root, k).exists() for k in keys)
    code, out = run("compact-objects", "--level", "15", "--recompress", "--jobs", "2", capsys=capsys)
    assert code == 0 and "0 compressed" in out
    code, out = run("compact-objects", "--level", "23", capsys=capsys)
    assert code == 2
    plain_path(root, keys[0]).write_bytes(b"tampered")  # a damaged leftover copy next to a sound .zst
    code, out = run("compact-objects", capsys=capsys)
    assert code == 0 and "1 leftover uncompressed copies removed" in out
    assert LocalObjectStore(root).get(keys[0]) == XML


# --------------------------------------------------------------------------- verify-sources on a mixed store


@pytest.mark.db
def test_verify_sources_passes_on_a_store_holding_both_kinds(
    session: Session, catalog: Catalog, store_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path / "objects"
    shutil.copytree(store_dir, root)
    store = LocalObjectStore(root)
    docs = session.scalars(select(m.SourceDocument).where(m.SourceDocument.source_id == SAMPLE)).all()
    assert len(docs) >= 4
    for doc in docs[::2]:  # every other object back to the legacy, uncompressed form
        path = store.locate(doc.storage_key)
        assert path is not None and path.suffix == ".zst"
        plain = plain_path(root, doc.storage_key)
        plain.write_bytes(zstandard.ZstdDecompressor().decompress(path.read_bytes()))
        path.unlink()
    kinds = {p.suffix for d in docs if (p := store.locate(d.storage_key)) is not None}
    assert kinds == {"", ".zst"}
    for doc in docs:
        result = verify_source_document(session, catalog, store, doc.id, checker="pytest")
        assert result["result"] == "pass", result
    assert store.compact(12).converted == len(docs[::2])
    for doc in docs:
        assert verify_source_document(session, catalog, store, doc.id, checker="pytest")["result"] == "pass"


# --------------------------------------------------------------------------- prune-cache decisions (no database)


def touch(path: Path, data: bytes = b"cached") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def names(monkeypatch: pytest.MonkeyPatch) -> dict[str, tuple[set[str], set[str]]]:
    """The cache directories each source names, set by the test (the real ones come from the manifests)."""
    table: dict[str, tuple[set[str], set[str]]] = {}

    def fake(sid: str, catalog: Catalog, base_dir: Path = REPO_ROOT) -> tuple[set[str], set[str]]:
        return table.get(sid, (set(), set()))

    monkeypatch.setattr(cache, "source_cache_names", fake)
    return table


def test_source_states_say_why_downloads_stay() -> None:
    assert SourceState("a", 3, 3, 0, "completed").problem is None
    assert SourceState("a", 3, 3, 0, None).problem is None  # imported corpus: no run recorded, all verified
    assert "not verified yet" in (SourceState("a", 3, 2, 0, "completed").problem or "")
    assert "failed verify-sources" in (SourceState("a", 3, 3, 1, "completed").problem or "")
    assert "completed_with_errors" in (SourceState("a", 3, 3, 0, "completed_with_errors").problem or "")
    assert "no stored documents" in (SourceState("a", 0, 0, 0, "failed").problem or "")
    assert not SourceState("a").loaded and SourceState("a", 0, 0, 0, "failed").loaded


def test_prune_plan_deletes_only_fully_verified_sources_and_keeps_shared_hosts(
    tmp_path: Path, catalog: Catalog, names: dict[str, tuple[set[str], set[str]]]
) -> None:
    ok, unverified, failed, never = "us-ecfr", "gb-legislation-gov-uk", "fi-finlex", "us-govinfo-uscourts"
    names[ok] = ({"www.govinfo.gov", "govinfo.gov"}, set())
    names[never] = ({"www.govinfo.gov", "govinfo.gov"}, set())  # never loaded: does not hold the directory back
    names[unverified] = ({"www.legislation.gov.uk", "legislation.gov.uk"}, set())
    names[failed] = ({"www.finlex.fi", "finlex.fi"}, set())
    names["fi-finlex-dpo-decisions"] = ({"www.finlex.fi"}, set())
    names["ca-justice-laws-xml"] = (set(), {"justicecanada__laws-lois-xml"})
    states = {
        ok: SourceState(ok, 50, 50, 0, "completed"),
        unverified: SourceState(unverified, 10, 9, 0, "completed"),
        failed: SourceState(failed, 10, 10, 2, "completed"),
        "fi-finlex-dpo-decisions": SourceState("fi-finlex-dpo-decisions", 5, 5, 0, "completed"),
        "ca-justice-laws-xml": SourceState("ca-justice-laws-xml", 7, 7, 0, "completed"),
    }
    cache_dir = tmp_path / "cache"
    ecfr = touch(cache_dir / "www.govinfo.gov/bulkdata/ECFR/title-1/ECFR-title1.xml", b"x" * 1000)
    robots = touch(cache_dir / "www.govinfo.gov/robots.txt", b"User-agent: *\nCrawl-delay: 2\n")
    touch(cache_dir / "www.govinfo.gov/robots.txt.meta.json", b"{}")
    uk = touch(cache_dir / "www.legislation.gov.uk/ukpga/2020/1/data.akn")
    fi = touch(cache_dir / "www.finlex.fi/files/open-data/x.zip")
    clone = touch(cache_dir / "justicecanada__laws-lois-xml/.git/objects/pack/p.pack", b"p" * 300)
    outside = touch(tmp_path / "elsewhere/keep.xml")
    (cache_dir / "www.govinfo.gov/link").symlink_to(outside.parent, target_is_directory=True)
    touch(cache_dir / "unknown.example.org/file.xml")  # a host no manifest names: never touched

    plan = build_plan(states, catalog, cache_dir)
    assert sorted(d.name for d in plan.prune) == ["justicecanada__laws-lois-xml", "www.govinfo.gov"]
    # The Finlex directory is named by a prunable source too, but the failed one keeps it whole; the UK directory
    # belongs to a source that is not fully verified and is not even considered.
    kept = {d.name: d.blocked_by for d in plan.keep}
    assert set(kept) == {"www.finlex.fi"}
    assert kept["www.finlex.fi"] == ["fi-finlex: 2 document(s) failed verify-sources"]
    assert plan.files == 2 and plan.size == 1300  # robots.txt, its metadata and the symlink are not counted

    dry = cache.format_prune(plan, dry_run=True)
    assert "would free" in dry and "dry run: nothing deleted" in dry and "kept www.finlex.fi" in dry
    assert "1 of 10 document(s) not verified yet" in dry  # the per-source table says why the UK stays
    assert ecfr.exists() and clone.exists()  # planning deletes nothing

    assert cache.apply_prune(plan) == (2, 1300)
    assert not ecfr.exists() and not (cache_dir / "www.govinfo.gov/bulkdata").exists()
    assert robots.exists() and (cache_dir / "www.govinfo.gov/robots.txt.meta.json").exists()
    assert not (cache_dir / "justicecanada__laws-lois-xml").exists()
    assert uk.exists() and fi.exists() and outside.exists() and (cache_dir / "unknown.example.org/file.xml").exists()

    only = build_plan(states, catalog, cache_dir, only=[unverified])  # --only a source that is not prunable
    assert only.prune == [] and only.keep == []


def test_robots_delay_still_reads_from_a_pruned_host(tmp_path: Path) -> None:
    touch(tmp_path / "www.legislation.gov.uk/robots.txt", b"User-agent: *\nCrawl-delay: 5\n")
    assert loading.cached_robots_delay(tmp_path, "www.legislation.gov.uk") == 5.0
    assert loading.cache_names(["www.finlex.fi"]) == {"www.finlex.fi", "finlex.fi"}


# --------------------------------------------------------------------------- prune-cache, load-all (PostgreSQL)


@pytest.fixture
def db_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, db_url: str, store_dir: Path, ingested: IngestionReport
) -> Path:
    monkeypatch.setenv("ULIS_DATABASE_URL", db_url)
    monkeypatch.setenv("ULIS_OBJECT_STORE_DIR", str(store_dir))
    monkeypatch.setenv("ULIS_SOURCE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ULIS_ENV", "development")
    monkeypatch.delenv("ULIS_API_KEYS", raising=False)
    return tmp_path / "cache"


BLOCKER = "ch-fedlex-treaties"  # stands for a source that failed; its run row is removed after the test


@pytest.mark.db
def test_prune_cache_command_and_load_all_option(
    db_env: Path,
    catalog: Catalog,
    store: LocalObjectStore,
    session_factory: sessionmaker[Session],
    names: dict[str, tuple[set[str], set[str]]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names[SAMPLE] = ({"www.sample.example", "shared.example"}, set())
    names[BLOCKER] = ({"shared.example"}, set())
    sample_file = touch(db_env / "www.sample.example/acts/act.xml", b"a" * 400)
    robots = touch(db_env / "www.sample.example/robots.txt", b"User-agent: *\n")
    shared = touch(db_env / "shared.example/data.json", b"{}")
    with session_factory() as s:
        failed_run = m.IngestionRun(source_id=BLOCKER, actor="pytest", status="failed", errors=["HTTP 503"])
        s.add(failed_run)
        s.commit()
        run_id = failed_run.id
    try:
        assert cache.verify_unverified(session_factory, catalog, store, [SAMPLE], "pytest", print) == 0
        code, out = run("prune-cache", "--dry-run", capsys=capsys)
        assert code == 0, out
        assert "would free" in out and "www.sample.example" in out and sample_file.exists()
        kept_line = next(line for line in out.splitlines() if line.startswith("kept shared.example: shared with"))
        assert BLOCKER in kept_line and "failed" in kept_line

        code, out = run("prune-cache", "--only", SAMPLE, capsys=capsys)
        assert code == 0 and "freed 400 B" in out, out
        assert not sample_file.exists() and robots.exists() and shared.exists()

        code, out = run("prune-cache", "--only", "no-such-source", capsys=capsys)
        assert code == 2

        # load-all --prune-cache: the sample loads (nothing new), then its downloads go.
        touch(db_env / "www.sample.example/acts/again.xml", b"b" * 100)
        code, out = run("load-all", "--only", SAMPLE, "--prune-cache", capsys=capsys)
        assert code == 0, out
        assert "freed 100 B" in out and not (db_env / "www.sample.example/acts").exists()

        # ... and nothing is pruned when a source of the run failed.
        def flaky(factory: Any, catalog: Catalog, store: Any, source_id: str, cache_dir: Path, actor: str) -> Any:
            raise RuntimeError("publisher refused the request (HTTP 403)")

        kept = touch(db_env / "www.sample.example/acts/kept.xml")
        monkeypatch.setattr(loading, "ingest_one", flaky)
        code, out = run("load-all", "--only", SAMPLE, "--prune-cache", capsys=capsys)
        assert code == 1 and "prune-cache: not run" in out and kept.exists()
    finally:
        with session_factory() as s:
            s.execute(delete(m.IngestionRun).where(m.IngestionRun.id == run_id))
            s.execute(
                delete(m.IngestionRun).where(m.IngestionRun.source_id == SAMPLE, m.IngestionRun.status == "failed")
            )
            s.commit()


@pytest.mark.db
def test_load_all_prune_cache_verifies_new_documents_first(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> None:
    with session_factory() as s:
        before = len(cache.unverified_documents(s, [SAMPLE]))
    lines: list[str] = []
    assert cache.verify_unverified(session_factory, catalog, store, [SAMPLE], "pytest", lines.append) == 0
    with session_factory() as s:
        assert cache.unverified_documents(s, [SAMPLE]) == []
        states = cache.source_states(s)
    assert states[SAMPLE].verified == states[SAMPLE].documents and states[SAMPLE].failed == 0
    assert (before == 0) == (lines == [])
