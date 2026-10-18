"""Command-line interface: ``python -m ulis.cli <command>``."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from ulis.catalog import Catalog
from ulis.db import make_engine, make_session_factory, session_scope
from ulis.db import models as m
from ulis.ingestion.catalog_sync import sync_catalog
from ulis.ingestion.pipeline import Ingestor
from ulis.ops.commands import OPS_COMMANDS, add_ops_parsers, run_ops_command
from ulis.ops.text import human_bytes
from ulis.retrieval.indexer import rebuild_passages
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import (
    DEFAULT_OBJECT_COMPRESSION_LEVEL,
    MAX_OBJECT_COMPRESSION_LEVEL,
    REPO_ROOT,
    Settings,
    load_settings,
)
from ulis.sources.registry import LicenceNotApproved
from ulis.storage.object_store import LocalObjectStore, open_object_store
from ulis.verification.integrity import verify_source_document


def migrate(database_url: str) -> None:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


def _research(args: argparse.Namespace, settings: Settings, catalog: Catalog) -> int:
    from ulis.assistant.config import MAX_CASE_CHARS, MAX_QUESTION_CHARS, load_assistant_config
    from ulis.assistant.oneshot import NotConfigured, run_once
    from ulis.assistant.pipeline import PipelineError, ResearchInput
    from ulis.assistant.safety import clean_text

    case_text: str | None = None
    if args.case_file:
        case_text = sys.stdin.read() if args.case_file == "-" else Path(args.case_file).read_text("utf-8")
    jurisdiction = args.jurisdiction.strip().upper() if args.jurisdiction else None
    if jurisdiction and jurisdiction not in catalog.jurisdictions:
        print(f"unknown jurisdiction {jurisdiction!r}", file=sys.stderr)
        return 2
    if len(args.question) > MAX_QUESTION_CHARS or len(case_text or "") > MAX_CASE_CHARS:
        print(
            f"question is limited to {MAX_QUESTION_CHARS} and the case to {MAX_CASE_CHARS} characters", file=sys.stderr
        )
        return 2
    request = ResearchInput(
        question=clean_text(args.question).strip(),
        case_text=clean_text(case_text).strip() if case_text else None,
        jurisdiction=jurisdiction,
        as_of=args.as_of,
        depth=args.depth,
    )

    def progress(stage: str, message: str) -> None:
        print(f"[{stage}] {message}", file=sys.stderr)

    try:
        result = run_once(settings, catalog, load_assistant_config(), request, report=progress)
    except NotConfigured as exc:
        print(f"research assistant unavailable: {exc}", file=sys.stderr)
        return 2
    except PipelineError as exc:
        print(f"research failed ({exc.code}): {exc.message}", file=sys.stderr)
        return 1
    print(result.model_dump_json(indent=2))
    return 0


def _compact_objects(args: argparse.Namespace, settings: Settings) -> int:
    level = args.level if args.level is not None else settings.object_compression_level
    if level == 0 and args.level is None:
        level = DEFAULT_OBJECT_COMPRESSION_LEVEL  # the store is set to write uncompressed; compaction still compresses
    if not 1 <= level <= MAX_OBJECT_COMPRESSION_LEVEL:
        print(f"--level must be 1 to {MAX_OBJECT_COMPRESSION_LEVEL}", file=sys.stderr)
        return 2
    store = LocalObjectStore(settings.object_store_dir, compression_level=level)

    def progress(done: int) -> None:
        if done % 1000 == 0:
            print(f"... {done:,} objects", file=sys.stderr, flush=True)

    report = store.compact(level, recompress=args.recompress, jobs=max(1, min(args.jobs, 32)), progress=progress)
    saved = report.bytes_before - report.bytes_after
    share = f" ({100 * saved / report.bytes_before:.1f}%)" if report.bytes_before else ""
    print(
        f"compact-objects (zstd level {level}) {settings.object_store_dir}: {report.objects:,} objects, "
        f"{report.converted:,} compressed, {report.recompressed:,} recompressed, {report.unchanged:,} unchanged, "
        f"{report.duplicates_removed:,} leftover uncompressed copies removed, "
        f"{report.stale_tmp_removed:,} stale temporary files removed"
    )
    print(
        f"bytes before {report.bytes_before:,} ({human_bytes(report.bytes_before)}), after {report.bytes_after:,} "
        f"({human_bytes(report.bytes_after)}), saved {human_bytes(saved)}{share}"
    )
    for error in report.errors[:20]:
        print(f"ERROR {error}", file=sys.stderr)
    if len(report.errors) > 20:
        print(f"... {len(report.errors) - 20} more errors", file=sys.stderr)
    return 1 if report.errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ulis")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate-config", help="validate profiles, sources and concepts")
    sub.add_parser("migrate", help="apply database migrations")
    sub.add_parser("sync-catalog", help="load profiles, courts, sources and concepts into the database")
    ing = sub.add_parser("ingest", help="ingest a source (licence-gated)")
    ing.add_argument("source_id")
    ing.add_argument("--actor", default="cli")
    sub.add_parser("reindex", help="rebuild the passages index from canonical tables")
    sub.add_parser("verify-sources", help="re-derive every stored text from its raw source document")
    add_ops_parsers(sub)  # load-all, status, prune-cache (ulis/ops)
    cmp = sub.add_parser(
        "compact-objects",
        help="store the raw source documents zstd-compressed (lossless, in place, resumable)",
        description=(
            "Converts the uncompressed objects of the object store to zstd (<digest>.zst), each one replaced only "
            "after the new file was written, read back and its SHA-256 checked. Idempotent: run it again to resume. "
            "Keys (the SHA-256 of the original bytes) do not change."
        ),
    )
    cmp.add_argument(
        "--level",
        type=int,
        metavar="N",
        help=f"zstd level 1-22 (default: ULIS_OBJECT_COMPRESSION_LEVEL, else {DEFAULT_OBJECT_COMPRESSION_LEVEL})",
    )
    cmp.add_argument(
        "--recompress",
        action="store_true",
        help="also rewrite compressed objects at --level when that makes them smaller (e.g. to go from 9 to 19)",
    )
    cmp.add_argument("--jobs", type=int, default=1, metavar="N", help="objects compressed at the same time (default 1)")
    srch = sub.add_parser("search", help="search the corpus")
    srch.add_argument("q")
    srch.add_argument("--jurisdiction")
    srch.add_argument("--as-of", type=date.fromisoformat)
    srch.add_argument("--forum-court")
    srch.add_argument("--include-foreign", action="store_true")
    srch.add_argument("--include-proposed", action="store_true")
    srch.add_argument("--include-historical", action="store_true")
    srch.add_argument("--limit", type=int, default=5)
    srch.add_argument("--json", action="store_true")
    rsch = sub.add_parser("research", help="run the LLM research assistant once (needs ANTHROPIC_API_KEY)")
    rsch.add_argument("--question", required=True)
    rsch.add_argument("--case-file", help="text file with the case ('-' reads standard input)")
    rsch.add_argument("--jurisdiction")
    rsch.add_argument("--as-of", type=date.fromisoformat)
    rsch.add_argument("--depth", choices=["standard", "deep"], default="standard")
    args = parser.parse_args(argv)

    settings = load_settings()
    catalog = Catalog.load(settings.config_dir)
    if args.cmd == "research":
        return _research(args, settings, catalog)
    if args.cmd == "validate-config":
        print(
            f"ok: {len(catalog.jurisdictions.profiles)} jurisdictions, {len(catalog.sources.sources)} sources, "
            f"{len(catalog.concepts)} concepts"
        )
        return 0
    if args.cmd == "migrate":
        migrate(settings.database_url)
        return 0

    if args.cmd in OPS_COMMANDS:
        return run_ops_command(args, settings, catalog)
    if args.cmd == "compact-objects":
        return _compact_objects(args, settings)

    factory = make_session_factory(make_engine(settings.database_url))
    store = open_object_store(settings)
    with session_scope(factory) as session:
        if args.cmd == "sync-catalog":
            print(json.dumps(sync_catalog(session, catalog, actor="cli")))
        elif args.cmd == "ingest":
            sync_catalog(session, catalog, actor=args.actor)
            try:
                report = Ingestor(session, catalog, store, cache_dir=settings.source_cache_dir).ingest_source(
                    args.source_id, actor=args.actor
                )
            except LicenceNotApproved as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 2
            print(json.dumps(report.as_dict(), indent=2))
            return 1 if report.errors else 0
        elif args.cmd == "reindex":
            print(f"indexed {rebuild_passages(session, catalog)} passages")
        elif args.cmd == "verify-sources":
            failures = 0
            for doc_id in session.scalars(select(m.SourceDocument.id)):
                res = verify_source_document(session, catalog, store, uuid.UUID(str(doc_id)), checker="cli")
                failures += res["result"] != "pass"
                print(f"{res['result']:4}  {res['external_id']}  units={res['checked_units']}  {res['problems']}")
            return 1 if failures else 0
        elif args.cmd == "search":
            result = search(
                session,
                catalog,
                SearchRequest(
                    q=args.q,
                    jurisdiction=args.jurisdiction,
                    as_of=args.as_of,
                    forum_court=args.forum_court,
                    include_foreign=args.include_foreign,
                    include_proposed=args.include_proposed,
                    include_historical=args.include_historical,
                    limit=args.limit,
                ),
                today=date.today(),
            )
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
            else:
                for hit in result["results"]:
                    print(f"{hit['rank']}. {hit['citation']}  [{hit['authority']['label']}]")
                    print(f'   "{hit["text"]}"')
                    print(
                        f"   source: {hit['source']['source_name']} doc={hit['source']['document_id']} "
                        f"sha256={hit['source']['sha256'][:16]}..."
                    )
                    for lim in hit["limitations"]:
                        if lim["severity"] != "info":
                            print(f"   ! {lim['code']}: {lim['message']}")
                for lim in result["limitations"]:
                    print(f"* {lim['severity'].upper()} {lim['code']}: {lim['message']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
