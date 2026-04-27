#!/bin/sh
set -eu

if [ "${DIT_SERVER_AUTO_MIGRATE:-1}" != "0" ]; then
  echo "Running Dit database migrations..."
  alembic -c /app/src/dit/server/alembic.ini upgrade head
fi

mkdir -p "${DIT_SERVER_DATA_DIR:-/data/dit}"

exec "$@"
