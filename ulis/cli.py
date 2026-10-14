"""Command-line interface: ``python -m ulis.cli <command>``."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from ulis.catalog import Catalog
from ulis.db import make_engine, make_session_factory, session_scope
from ulis.db import models as m
from ulis.ingestion.catalog_sync import sync_catalog
from ulis.ingestion.pipeline import Ingestor
from ulis.retrieval.indexer import rebuild_passages
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT, load_settings
from ulis.sources.registry import LicenceNotApproved
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document


def migrate(database_url: str) -> None:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


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
    args = parser.parse_args(argv)

    settings = load_settings()
    catalog = Catalog.load(settings.config_dir)
    if args.cmd == "validate-config":
        print(
            f"ok: {len(catalog.jurisdictions.profiles)} jurisdictions, {len(catalog.sources.sources)} sources, "
            f"{len(catalog.concepts)} concepts"
        )
        return 0
    if args.cmd == "migrate":
        migrate(settings.database_url)
        return 0

    factory = make_session_factory(make_engine(settings.database_url))
    store = LocalObjectStore(settings.object_store_dir)
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
