#!/usr/bin/env bash
set -euo pipefail

BACKUP_PATH="${1:-${DIT_RESTORE_BACKUP:-}}"
DATABASE_URL="${DIT_RESTORE_DATABASE_URL:-${DIT_SERVER_DATABASE_URL:-}}"
DATA_DIR="${DIT_RESTORE_DATA_DIR:-${DIT_SERVER_DATA_DIR:-}}"
CONFIRM_TEXT="I_UNDERSTAND_THIS_OVERWRITES_DATA"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 2
  fi
}

to_pg_url() {
  python3 - "$1" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit

parts = urlsplit(sys.argv[1])
scheme = parts.scheme.split("+", 1)[0]
if scheme not in {"postgresql", "postgres"}:
    raise SystemExit(f"unsupported database URL scheme: {parts.scheme}")
print(urlunsplit(("postgresql", parts.netloc, parts.path, parts.query, parts.fragment)))
PY
}

verify_checksums() {
  if [ ! -f "$BACKUP_PATH/checksums.sha256" ]; then
    echo "missing checksums.sha256 in backup: $BACKUP_PATH" >&2
    exit 1
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    (cd "$BACKUP_PATH" && sha256sum -c checksums.sha256)
  else
    (cd "$BACKUP_PATH" && shasum -a 256 -c checksums.sha256)
  fi
}

if [ "${DIT_RESTORE_CONFIRM:-}" != "$CONFIRM_TEXT" ]; then
  echo "refusing restore; set DIT_RESTORE_CONFIRM=$CONFIRM_TEXT" >&2
  exit 2
fi

need pg_restore
need tar
need python3
if [ -z "$BACKUP_PATH" ] || [ ! -d "$BACKUP_PATH" ]; then
  echo "backup directory is required as argv[1] or DIT_RESTORE_BACKUP" >&2
  exit 2
fi
if [ -z "$DATABASE_URL" ]; then
  echo "DIT_SERVER_DATABASE_URL or DIT_RESTORE_DATABASE_URL is required" >&2
  exit 2
fi
if [ -z "$DATA_DIR" ]; then
  echo "DIT_SERVER_DATA_DIR or DIT_RESTORE_DATA_DIR is required" >&2
  exit 2
fi
if [ ! -f "$BACKUP_PATH/postgres.dump" ] || [ ! -f "$BACKUP_PATH/data-dir.tar.gz" ]; then
  echo "backup is missing postgres.dump or data-dir.tar.gz: $BACKUP_PATH" >&2
  exit 1
fi

verify_checksums

PG_URL="$(to_pg_url "$DATABASE_URL")"
echo "restoring PostgreSQL database from $BACKUP_PATH/postgres.dump"
pg_restore --clean --if-exists --no-owner --dbname "$PG_URL" "$BACKUP_PATH/postgres.dump"

if [ -d "$DATA_DIR" ] && [ "$(find "$DATA_DIR" -mindepth 1 -maxdepth 1 | head -1)" ]; then
  if [ "${DIT_RESTORE_OVERWRITE_DATA_DIR:-0}" != "1" ]; then
    echo "data directory is not empty: $DATA_DIR" >&2
    echo "set DIT_RESTORE_OVERWRITE_DATA_DIR=1 to move it aside before restore" >&2
    exit 2
  fi
  TS="$(date -u +%Y%m%dT%H%M%SZ)"
  OLD="${DATA_DIR}.before-restore-${TS}"
  echo "moving existing data directory to $OLD"
  mv "$DATA_DIR" "$OLD"
fi

mkdir -p "$DATA_DIR"
echo "restoring object data dir into $DATA_DIR"
tar -xzf "$BACKUP_PATH/data-dir.tar.gz" -C "$DATA_DIR"

echo "restore complete"
