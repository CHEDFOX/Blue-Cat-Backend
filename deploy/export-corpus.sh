#!/usr/bin/env bash
# Export the loaded CORPUS so another machine can import it instead of re-downloading for hours.
#
#   deploy/export-corpus.sh [--out DIR] [--with-objects]
#
# Writes DIR/ulis-corpus-<date>.dump (pg_dump, custom format) plus .sha256 and a .txt summary. The archive holds the
# schema and the data of the corpus tables only (see CORPUS_TABLES in lib.sh): sources, source documents, instruments,
# provisions, judgments, citations, concepts and the search passages. Audit events, verification records, research
# jobs and any other table are exported EMPTY, so no usage data leaves the machine.
# --with-objects also archives the raw source documents (DIR/ulis-corpus-<date>-objects.tar.gz). They are what
# `verify-sources` and the integrity endpoint re-derive texts from; without them the imported corpus is searchable
# but cannot be re-verified until the documents are fetched again (load-all re-fetches missing ones from the cache
# or the publishers).
# Import on the other machine with deploy/import-corpus.sh.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

OUT="$DEPLOY_DIR/exports"; WITH_OBJECTS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --with-objects) WITH_OBJECTS=1; shift ;;
    -h|--help) sed -n '2,/^set -e/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done
[ "$ULIS_PG_MODE" = compose ] && need docker
[ "$ULIS_PG_MODE" = direct ] && need pg_dump psql
umask 077
mkdir -p "$OUT"
stamp="$(date -u +%Y%m%d)"
file="$OUT/ulis-corpus-$stamp.dump"
trap 'rm -f "$file.partial" "$OUT/ulis-corpus-$stamp-objects.tar.gz.partial"' EXIT

# Every table in the database that is not a corpus table: its data is left out of the dump (its definition stays,
# so the archive restores into a complete, empty schema and alembic keeps working).
exclude=()
while IFS= read -r table; do
  [ -n "$table" ] && exclude+=("--exclude-table-data=public.$table")
done < <(pg psql --no-psqlrc -Atc "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1" \
         | grep -v -x -F "$(printf '%s\n' $CORPUS_TABLES)" || true)

log "dumping schema and corpus data to $file (excluded data: ${#exclude[@]} table(s))"
pg pg_dump --format=custom --compress=6 --no-owner "${exclude[@]}" > "$file.partial"
[ -s "$file.partial" ] || die "the dump is empty"
pg pg_restore --list < "$file.partial" > /dev/null || die "the dump cannot be read back"
mv "$file.partial" "$file"

{
  echo "ULIS corpus export, $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "alembic revision: $(pg psql --no-psqlrc -Atc 'SELECT version_num FROM alembic_version' | tr '\n' ' ')"
  for t in sources source_documents instruments instrument_versions provisions provision_versions judgments passages; do
    echo "$t: $(pg psql --no-psqlrc -Atc "SELECT count(*) FROM $t")"
  done
} > "$file.txt"

if [ "$WITH_OBJECTS" = 1 ]; then
  src="$(objects_dir)"
  [ -d "$src" ] || die "object store directory $src not found (set ULIS_OBJECT_STORE_DIR or ULIS_DATA_DIR)"
  log "archiving the object store $src"
  obj="$OUT/ulis-corpus-$stamp-objects.tar.gz"
  tar -C "$src" -czf "$obj.partial" .
  mv "$obj.partial" "$obj"
fi
( cd "$OUT" && sha256sum "$(basename "$file")" > "$(basename "$file").sha256" )
cat "$file.txt" >&2
printf '%s\n' "$file"
