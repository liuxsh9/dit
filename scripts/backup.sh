#!/usr/bin/env bash
set -euo pipefail

BACKUP_ROOT="${DIT_BACKUP_DIR:-}"
DATABASE_URL="${DIT_BACKUP_DATABASE_URL:-${DIT_SERVER_DATABASE_URL:-}}"
DATA_DIR="${DIT_BACKUP_DATA_DIR:-${DIT_SERVER_DATA_DIR:-}}"
BACKUP_NAME="${DIT_BACKUP_NAME:-dit-core-$(date -u +%Y%m%dT%H%M%SZ)}"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 2
  fi
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$@"
  else
    shasum -a 256 "$@"
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

redact_url() {
  python3 - "$1" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit

parts = urlsplit(sys.argv[1])
netloc = parts.netloc
if "@" in netloc:
    userinfo, host = netloc.rsplit("@", 1)
    user = userinfo.split(":", 1)[0]
    netloc = f"{user}:***@{host}"
print(urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment)))
PY
}

need pg_dump
need tar
need python3

if [ -z "$BACKUP_ROOT" ]; then
  echo "DIT_BACKUP_DIR is required" >&2
  exit 2
fi
if [ -z "$DATABASE_URL" ]; then
  echo "DIT_SERVER_DATABASE_URL or DIT_BACKUP_DATABASE_URL is required" >&2
  exit 2
fi
if [ -z "$DATA_DIR" ]; then
  echo "DIT_SERVER_DATA_DIR or DIT_BACKUP_DATA_DIR is required" >&2
  exit 2
fi
if [ ! -d "$DATA_DIR" ]; then
  echo "data directory does not exist: $DATA_DIR" >&2
  exit 1
fi
if [ "${DIT_BACKUP_CONFIRM_QUIESCED:-0}" != "1" ] && [ "${DIT_BACKUP_ALLOW_ONLINE:-0}" != "1" ]; then
  cat >&2 <<'EOF'
Refusing to take a backup without a consistency decision.

For a fully consistent backup, stop dit-core or otherwise block writes, then set:
  DIT_BACKUP_CONFIRM_QUIESCED=1

For a best-effort online backup, set:
  DIT_BACKUP_ALLOW_ONLINE=1
EOF
  exit 2
fi

if [ "${DIT_BACKUP_ALLOW_ONLINE:-0}" = "1" ]; then
  echo "warning: taking best-effort online backup; concurrent writes may not be fully consistent" >&2
fi

DEST="${BACKUP_ROOT%/}/$BACKUP_NAME"
if [ -e "$DEST" ]; then
  echo "backup destination already exists: $DEST" >&2
  exit 1
fi
mkdir -p "$DEST"
BACKUP_COMPLETE=0
cleanup_incomplete_backup() {
  if [ "$BACKUP_COMPLETE" != "1" ]; then
    rm -rf "$DEST"
  fi
}
trap cleanup_incomplete_backup EXIT

PG_URL="$(to_pg_url "$DATABASE_URL")"
echo "dumping PostgreSQL database to $DEST/postgres.dump"
pg_dump --format=custom --file "$DEST/postgres.dump" "$PG_URL"

echo "archiving object data dir to $DEST/data-dir.tar.gz"
tar -czf "$DEST/data-dir.tar.gz" -C "$DATA_DIR" .

(
  cd "$DEST"
  sha256_file postgres.dump data-dir.tar.gz > checksums.sha256
)

python3 - "$DEST/manifest.json" <<PY
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

def git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return None

manifest = {
    "created_at": datetime.now(timezone.utc).isoformat(),
    "backup_name": os.environ.get("BACKUP_NAME", "$BACKUP_NAME"),
    "database_url": "$(redact_url "$DATABASE_URL")",
    "data_dir": "$DATA_DIR",
    "consistency": "online-best-effort" if os.environ.get("DIT_BACKUP_ALLOW_ONLINE") == "1" else "quiesced",
    "git_commit": git_commit(),
    "files": ["postgres.dump", "data-dir.tar.gz", "checksums.sha256"],
}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2, sort_keys=True)
    f.write("\n")
PY

BACKUP_COMPLETE=1
echo "backup complete: $DEST"
