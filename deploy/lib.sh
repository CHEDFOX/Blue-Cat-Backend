#!/usr/bin/env bash
# Shared helpers for the deploy/*.sh scripts. Source it; do not run it.
#
# Where PostgreSQL is:
#   compose (default): the `db` service of deploy/docker-compose.yml, reached with `docker compose exec`, so
#                      pg_dump / pg_restore always match the server's version and nothing is installed on the host.
#   direct:            PostgreSQL client tools on this machine. Chosen automatically when ULIS_PG_URL or PGHOST is
#                      set (or ULIS_PG_MODE=direct). ULIS_PG_URL is a libpq URI, e.g. postgresql://user:pw@host:5432/db;
#                      it is derived from ULIS_DATABASE_URL (dropping "+psycopg") when only that is set.

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# env_get NAME [DEFAULT]: the environment variable if set, else its value in deploy/.env, else DEFAULT.
# (.env is parsed, never sourced, so a stray character in a value cannot run code.)
env_get() {
  local name="$1" default="${2:-}" value="${!1:-}"
  if [ -z "$value" ] && [ -f "$DEPLOY_DIR/.env" ]; then
    value="$(grep -E "^${name}=" "$DEPLOY_DIR/.env" | tail -n1 | cut -d= -f2- || true)"
    value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
  fi
  printf '%s' "${value:-$default}"
}

if [ -z "${ULIS_PG_URL:-}" ] && [ -n "${ULIS_DATABASE_URL:-}" ]; then
  ULIS_PG_URL="${ULIS_DATABASE_URL/+psycopg/}"
fi
if [ -z "${ULIS_PG_MODE:-}" ]; then
  if [ -n "${ULIS_PG_URL:-}" ] || [ -n "${PGHOST:-}" ]; then ULIS_PG_MODE=direct; else ULIS_PG_MODE=compose; fi
fi

compose() { docker compose --project-directory "$DEPLOY_DIR" -f "$DEPLOY_DIR/docker-compose.yml" "$@"; }

# pg TOOL [ARGS...]: run a PostgreSQL client tool (pg_dump, pg_restore, psql) against the ULIS database.
# Data goes through stdin/stdout, so `pg pg_dump -Fc > file` and `pg pg_restore < file` work in both modes.
pg() {
  local tool="$1"; shift
  if [ "$ULIS_PG_MODE" = compose ]; then
    compose exec -T db "$tool" -U ulis -d ulis "$@"
  elif [ -n "${ULIS_PG_URL:-}" ]; then
    "$tool" -d "$ULIS_PG_URL" "$@"
  else
    "$tool" "$@"   # libpq reads PGHOST, PGUSER, PGDATABASE, PGPASSWORD ...
  fi
}

# Host directory holding the raw source documents (object store).
objects_dir() {
  if [ -n "${ULIS_OBJECT_STORE_DIR:-}" ]; then printf '%s' "$ULIS_OBJECT_STORE_DIR"
  else printf '%s/objects' "$(env_get ULIS_DATA_DIR /srv/ulis)"; fi
}

# The tables whose DATA is "the corpus": the law itself, where it came from, and the search index over it.
# Everything else (audit events, verification records, research jobs, propositions, ...) is usage data and is
# NOT exported by export-corpus.sh; a table added by a later migration is excluded until listed here.
CORPUS_TABLES="alembic_version jurisdictions jurisdiction_profile_versions courts sources source_documents ingestion_runs instruments instrument_versions provisions provision_versions provision_version_sources lifecycle_events treaty_parties judgments judgment_versions judgment_paragraphs citation_links legal_concepts concept_labels passages"

# need TOOL...: fail early with a clear message when a required program is missing.
need() { local t; for t in "$@"; do command -v "$t" >/dev/null 2>&1 || die "'$t' is required but not installed"; done; }

# confirm_destructive ASSUME_YES WHAT: ask the operator to type the database name unless --yes was given.
confirm_destructive() {
  local yes="$1" what="$2" answer
  [ "$yes" = 1 ] && return 0
  [ -t 0 ] || die "$what REPLACES the database contents; pass --yes to confirm when not running interactively"
  printf '%s\nType "ulis" to continue: ' "$what REPLACES the contents of the ULIS database." >&2
  read -r answer
  [ "$answer" = "ulis" ] || die "not confirmed"
}

# restore_dump FILE: replace the database with a pg_dump custom-format archive (all-or-nothing: one transaction).
# In compose mode the API is stopped meanwhile and started again afterwards (it migrates on start).
restore_dump() {
  local file="$1"
  pg pg_restore --list < "$file" > /dev/null || die "$file is not a readable pg_dump archive"
  if [ "$ULIS_PG_MODE" = compose ]; then
    log "stopping the api"
    compose stop api >&2
  else
    log "direct mode: make sure nothing else is using the database (stop the API)"
  fi
  log "restoring $file"
  if ! pg pg_restore --clean --if-exists --no-owner --single-transaction --exit-on-error < "$file"; then
    die "restore failed; the transaction was rolled back and the previous contents are intact (the api is still stopped)"
  fi
  if [ "$ULIS_PG_MODE" = compose ]; then
    log "starting the api (it applies pending migrations and syncs the catalog)"
    compose start api >&2
  fi
}
