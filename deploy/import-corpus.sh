#!/usr/bin/env bash
# Import a corpus exported by deploy/export-corpus.sh into THIS server's database (replacing its contents).
#
#   deploy/import-corpus.sh [--yes] [--objects ARCHIVE.tar.gz] DUMP
#
# Typical use on a fresh server, after `docker compose up -d` has created the (empty) database:
#   scp old-server:/path/ulis-corpus-20261014* .                # dump, .sha256, optionally the objects archive
#   deploy/import-corpus.sh --objects ulis-corpus-20261014-objects.tar.gz ulis-corpus-20261014.dump
#   docker compose --profile tools run --rm cli status
#   docker compose --profile tools run --rm cli verify-sources   # only meaningful with the objects archive
#
# It replaces everything in the database, including audit events and research results of THIS server, as one
# transaction (all or nothing). The API is stopped meanwhile and restarted afterwards; it applies migrations that
# are newer than the dump and re-syncs the catalog. The search passages come with the dump; if the text
# search configuration changed between versions run `cli reindex`.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

YES=0; OBJECTS=""; DUMP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) YES=1; shift ;;
    --objects) OBJECTS="$2"; shift 2 ;;
    -h|--help) sed -n '2,/^set -e/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option: $1 (try --help)" ;;
    *) [ -z "$DUMP" ] || die "only one dump file can be given"; DUMP="$1"; shift ;;
  esac
done
[ -n "$DUMP" ] || die "usage: import-corpus.sh [--yes] [--objects ARCHIVE] DUMP"
[ -f "$DUMP" ] || die "$DUMP not found"
[ -z "$OBJECTS" ] || [ -f "$OBJECTS" ] || die "$OBJECTS not found"
[ "$ULIS_PG_MODE" = compose ] && need docker
[ "$ULIS_PG_MODE" = direct ] && need pg_restore psql

if [ -f "$DUMP.sha256" ]; then
  ( cd "$(dirname "$DUMP")" && sha256sum --check --status "$(basename "$DUMP").sha256" ) || die "checksum mismatch: $DUMP is damaged"
  log "checksum ok"
fi
confirm_destructive "$YES" "Importing $DUMP"
restore_dump "$DUMP"
if [ -n "$OBJECTS" ]; then
  dest="$(objects_dir)"
  mkdir -p "$dest"
  log "unpacking $OBJECTS into $dest (the directory must belong to uid 10001 for the containers)"
  tar -C "$dest" -xf "$OBJECTS"   # GNU tar recognises gzip (or a plain .tar) by itself
fi
log "import finished: run 'cli status' to see what was loaded"
