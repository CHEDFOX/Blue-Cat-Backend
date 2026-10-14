"""Command-line entry points for the operator commands (registered by ``ulis.cli``)."""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

from ulis.catalog import Catalog
from ulis.db import make_engine, make_session_factory, session_scope
from ulis.ops import loading, status
from ulis.settings import Settings

OPS_COMMANDS = ("load-all", "status")


def add_ops_parsers(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    load = sub.add_parser(
        "load-all",
        help="ingest every active source, smallest first; resumable; continues after a failing source",
        description=(
            "Ingest every source whose status is active (the fictional sample only when named). Downloads are "
            "cached under ULIS_SOURCE_CACHE_DIR and ingestion is idempotent, so re-running resumes. "
            "Exit code: 0 ok, 1 a source errored, 2 usage, 3 another load-all is running, 130 interrupted."
        ),
    )
    load.add_argument("--only", nargs="+", default=[], metavar="SRC", help="load only these source ids")
    load.add_argument("--skip", nargs="+", default=[], metavar="SRC", help="do not load these source ids")
    load.add_argument(
        "--dry-run", action="store_true", help="print the plan (order, size, network time); change nothing"
    )
    load.add_argument(
        "--refresh",
        action="store_true",
        help="weekly maintenance: skip static pins and discard cached downloads older than --max-cache-age-days",
    )
    load.add_argument("--max-cache-age-days", type=float, default=7.0, metavar="DAYS")
    load.add_argument("--include-fictional", action="store_true", help="also load the fictional sample corpus")
    stat = sub.add_parser("status", help="per-jurisdiction counts, last ingestion and verification freshness")
    stat.add_argument("--json", action="store_true", help="machine-readable output")
    stat.add_argument("--all", action="store_true", help="also list jurisdictions without data")


def run_ops_command(args: argparse.Namespace, settings: Settings, catalog: Catalog) -> int:
    engine = make_engine(settings.database_url)
    try:
        return _run(args, settings, catalog, engine)
    except OperationalError as exc:  # never print the URL: it contains the password
        reason = str(exc.orig).strip().splitlines()[0] if exc.orig else "connection failed"
        print(f"error: cannot use the database: {reason}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


def _run(args: argparse.Namespace, settings: Settings, catalog: Catalog, engine: Engine) -> int:
    if args.cmd == "load-all":
        options = loading.LoadOptions(
            only=tuple(args.only),
            skip=tuple(args.skip),
            dry_run=args.dry_run,
            refresh=args.refresh,
            max_cache_age_days=args.max_cache_age_days,
            include_fictional=args.include_fictional,
        )
        return loading.run_load_all(settings, catalog, engine, options)
    with session_scope(make_session_factory(engine)) as session:
        data = status.collect_status(session, catalog, include_empty=args.all)
    if args.json:
        print(json.dumps(status.status_as_json(data), indent=2, ensure_ascii=False))
    else:
        print(status.render_status(data))
    return 0
