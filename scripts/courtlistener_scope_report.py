"""Count CourtListener clusters (cases) by court group, court, year and status, to size the scope of the bulk source.

Usage (from the repository root)::

    python scripts/courtlistener_scope_report.py --manifest config/manifests/us-courtlistener-bulk-s3.yaml \
        --cache var/sources --out var/courtlistener-scope.csv [--local-dir DIR]

It downloads the court, cluster and dockets files of the manifest's generation (about 7.7 GB; the citations and opinions
files are not needed and are not touched), streams them once and writes one CSV row per (court, source code, year,
status)
with the number of clusters, plus a summary on standard output. Clusters CourtListener marks as blocked are counted
apart. Nothing here is ingested.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from ulis.sources.connectors.courtlistener_bulk import (
    CourtListenerFiles,
    Scope,
    build_index,
    count_clusters,
    load_manifest,
    read_courts,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-id", default="us-courtlistener-bulk-s3")
    parser.add_argument("--cache", type=Path, default=Path("var/sources"))
    parser.add_argument("--local-dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--keep-index", action="store_true")
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest, args.source_id)
    files = CourtListenerFiles(manifest, args.cache, local_dir=args.local_dir)
    # Only the three files the report needs; the citations file is not read when keep_text is false.
    for key in ("courts", "clusters", "dockets"):
        print(f"file {key}: {files.url(key)}", file=sys.stderr)
        files.ensure(key)
    index = args.out.with_suffix(".sqlite")
    stats = build_index(files, Scope.everything(), index, keep_text=False, progress=lambda m: print(m, file=sys.stderr))
    courts = read_courts(files.path("courts"))
    rows = count_clusters(index)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["court_id", "group", "source", "year", "status", "clusters"])
        for court_id, source, year, status, n in sorted(rows):
            writer.writerow([court_id, courts.get(court_id, {}).get("jurisdiction", ""), source, year, status, n])
    by_group: Counter[str] = Counter()
    for court_id, _source, _year, _status, n in rows:
        by_group[courts.get(court_id, {}).get("jurisdiction", "?")] += n
    print(
        f"clusters read {stats.clusters_read:,}; with docket {sum(by_group.values()):,}; "
        f"blocked {stats.clusters_blocked:,}"
    )
    for group, n in by_group.most_common():
        print(f"  {group:4} {n:>12,}")
    if not args.keep_index:
        db = sqlite3.connect(index)
        db.close()
        index.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
