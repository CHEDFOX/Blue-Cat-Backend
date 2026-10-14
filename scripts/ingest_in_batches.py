"""Ingest one source in batches of documents, committing after each batch.

``python -m ulis.cli ingest <source>`` loads a whole source in ONE transaction, which is fine for a few hundred
documents but not for the French sources (thousands of codes and laws with up to 50,000 article versions each, or tens
of thousands of decisions). This script gives the same result in batches: the connector is read once, every
``--batch`` documents are ingested and committed (each batch is one ingestion run), and an interrupted run can be
restarted: documents already stored are skipped by their SHA-256 and cost one query each.

Usage (from the repository root, with ULIS_DATABASE_URL set)::

    python scripts/ingest_in_batches.py fr-dila-legi --batch 5
    python scripts/ingest_in_batches.py fr-dila-cass --batch 500 --manifest /tmp/slice.yaml --limit 2000

``--manifest`` replaces the source's committed manifest (for a trial on a slice); ``--limit`` stops after that many
documents; ``--metadata nature=CODE,LOI`` keeps only the documents whose connector metadata has one of these values
(here the nature of a LEGI text).
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path

from ulis.catalog import Catalog
from ulis.db import make_engine, make_session_factory, session_scope
from ulis.ingestion.catalog_sync import sync_catalog
from ulis.ingestion.pipeline import CONNECTORS, Ingestor, build_connector
from ulis.settings import load_settings
from ulis.sources.connectors.base import RawDocument
from ulis.storage.object_store import LocalObjectStore


class _Chunk:
    def __init__(self, documents: list[RawDocument]):
        self.documents = documents
        self.fetch_errors: list[str] = []

    def fetch(self) -> Iterator[RawDocument]:
        return iter(self.documents)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source_id")
    parser.add_argument("--batch", type=int, default=10)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--metadata", help="key=value[,value...]: keep documents whose metadata[key] is one of the values"
    )
    parser.add_argument("--actor", default="batch-cli")
    args = parser.parse_args(argv)

    settings = load_settings()
    catalog = Catalog.load(settings.config_dir)
    source = catalog.sources.get(args.source_id)
    if args.manifest is not None:
        factory = CONNECTORS[source.connector or ""]
        connector = factory(args.manifest, source.id, settings.source_cache_dir)
    else:
        connector = build_connector(source, cache_dir=settings.source_cache_dir)
    sessions = make_session_factory(make_engine(settings.database_url))
    store = LocalObjectStore(settings.object_store_dir)
    with session_scope(sessions) as session:
        sync_catalog(session, catalog, actor=args.actor)

    documents = connector.fetch()
    if args.metadata:
        key, _, values = args.metadata.partition("=")
        wanted = set(values.split(","))
        documents = (d for d in documents if str(d.metadata.get(key)) in wanted)
    if args.limit:
        documents = itertools.islice(documents, args.limit)
    totals: dict[str, int] = {}
    errors: list[str] = []
    started = time.time()
    batches = 0
    while True:
        chunk = list(itertools.islice(documents, args.batch))
        if not chunk:
            break
        with session_scope(sessions) as session:
            report = Ingestor(session, catalog, store, cache_dir=settings.source_cache_dir).ingest_source(
                args.source_id, actor=args.actor, connector=_Chunk(chunk)
            )
        batches += 1
        for key, value in report.as_dict().items():
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
        errors.extend(report.errors)
        print(
            f"batch {batches}: {totals.get('documents_seen', 0)} documents, "
            f"{totals.get('provision_versions_created', 0)} provision versions, "
            f"{totals.get('paragraphs_created', 0)} paragraphs, "
            f"{int(time.time() - started)} s, {len(report.errors)} errors",
            flush=True,
        )
    errors.extend(getattr(connector, "fetch_errors", []))
    print(
        json.dumps(
            {"totals": totals, "errors": errors[:50], "error_count": len(errors), "seconds": int(time.time() - started)}
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
