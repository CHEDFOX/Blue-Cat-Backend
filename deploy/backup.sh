#!/usr/bin/env bash
# Database backup: a compressed pg_dump (custom format) with retention. Safe for cron and systemd timers:
# progress goes to stderr, the path of the new dump is printed on stdout, any failure exits non-zero.
#
#   deploy/backup.sh [--dir DIR] [--keep-days N] [--with-objects]
#
# Settings (environment or deploy/.env): ULIS_BACKUP_DIR (default deploy/backups), ULIS_BACKUP_KEEP_DAYS (14),
# ULIS_BACKUP_OBJECTS (0). The newest 3 dumps are always kept whatever their age.
# The dump holds EVERYTHING in the database (including audit events and research results): treat it as sensitive.
# The raw object store (--with-objects) is content-addressed and immutable; for large stores prefer
# `rsync -a --ignore-existing /srv/ulis/objects/ backup-host:/path/objects/` to a tarball every night.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

BACKUP_DIR="$(env_get ULIS_BACKUP_DIR "$DEPLOY_DIR/backups")"
KEEP_DAYS="$(env_get ULIS_BACKUP_KEEP_DAYS 14)"
WITH_OBJECTS="$(env_get ULIS_BACKUP_OBJECTS 0)"
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) BACKUP_DIR="$2"; shift 2 ;;
    --keep-days) KEEP_DAYS="$2"; shift 2 ;;
    --with-objects) WITH_OBJECTS=1; shift ;;
    -h|--help) sed -n '2,/^set -e/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done
case "$KEEP_DAYS" in ''|*[!0-9]*) die "--keep-days must be a whole number" ;; esac
[ "$ULIS_PG_MODE" = compose ] && need docker
[ "$ULIS_PG_MODE" = direct ] && need pg_dump pg_restore

umask 077
mkdir -p "$BACKUP_DIR"
exec 9> "$BACKUP_DIR/.lock"
if command -v flock >/dev/null 2>&1; then flock -n 9 || die "another backup is running"; fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="$BACKUP_DIR/ulis-$stamp.dump"
trap 'rm -f "$out.partial" "$BACKUP_DIR/ulis-objects-$stamp.tar.gz.partial"' EXIT

log "dumping the database to $out"
pg pg_dump --format=custom --compress=6 --no-owner > "$out.partial"
[ -s "$out.partial" ] || die "the dump is empty"
pg pg_restore --list < "$out.partial" > /dev/null || die "the dump cannot be read back"
mv "$out.partial" "$out"
( cd "$BACKUP_DIR" && sha256sum "ulis-$stamp.dump" > "ulis-$stamp.dump.sha256" )
log "database dump done ($(du -h "$out" | cut -f1))"

if [ "$WITH_OBJECTS" = 1 ]; then
  src="$(objects_dir)"
  [ -d "$src" ] || die "object store directory $src not found (set ULIS_OBJECT_STORE_DIR or ULIS_DATA_DIR)"
  log "archiving the object store $src"
  tar -C "$src" -czf "$BACKUP_DIR/ulis-objects-$stamp.tar.gz.partial" .
  mv "$BACKUP_DIR/ulis-objects-$stamp.tar.gz.partial" "$BACKUP_DIR/ulis-objects-$stamp.tar.gz"
fi

# Retention: delete dumps (and their checksums / object archives) older than KEEP_DAYS, but never the newest 3.
n=0
for f in $(ls -1t "$BACKUP_DIR"/ulis-*.dump 2>/dev/null); do
  n=$((n + 1))
  [ "$n" -le 3 ] && continue
  if [ -n "$(find "$f" -mtime +"$KEEP_DAYS" -print)" ]; then
    name="$(basename "$f" .dump)"
    log "removing old backup $name"
    rm -f "$f" "$f.sha256" "$BACKUP_DIR/ulis-objects-${name#ulis-}.tar.gz"
  fi
done

printf '%s\n' "$out"
