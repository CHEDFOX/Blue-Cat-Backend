#!/usr/bin/env bash
# Restore a dump made by deploy/backup.sh. This REPLACES the contents of the ULIS database.
#
#   deploy/restore.sh [--yes] [--objects ARCHIVE.tar.gz] DUMP
#
# Runs as one transaction: if anything fails the previous contents stay as they were. In the compose setup the
# API is stopped for the duration and started again (it then applies any newer migrations). --objects also unpacks
# an object-store archive made with backup.sh --with-objects (into the host object store; run as a user who may
# write there, and keep the directory owned by uid 10001).
# After a restore:  docker compose --profile tools run --rm cli status
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
[ -n "$DUMP" ] || die "usage: restore.sh [--yes] [--objects ARCHIVE] DUMP"
[ -f "$DUMP" ] || die "$DUMP not found"
[ -z "$OBJECTS" ] || [ -f "$OBJECTS" ] || die "$OBJECTS not found"
[ "$ULIS_PG_MODE" = compose ] && need docker
[ "$ULIS_PG_MODE" = direct ] && need pg_restore psql

if [ -f "$DUMP.sha256" ]; then
  ( cd "$(dirname "$DUMP")" && sha256sum --check --status "$(basename "$DUMP").sha256" ) || die "checksum mismatch: $DUMP is damaged"
  log "checksum ok"
fi
confirm_destructive "$YES" "Restoring $DUMP"
restore_dump "$DUMP"
if [ -n "$OBJECTS" ]; then
  dest="$(objects_dir)"
  mkdir -p "$dest"
  log "unpacking $OBJECTS into $dest"
  tar -C "$dest" -xzf "$OBJECTS"
fi
log "restore finished"
