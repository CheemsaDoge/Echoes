#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
if [ ! -f .env ]; then echo 'Run python scripts/bootstrap_env.py first.' >&2; exit 1; fi
set -a
. ./.env
set +a
export PYTHONPATH="$PWD/backend${PYTHONPATH:+:$PYTHONPATH}"
PYTHON_BIN="python3"
if [ -x "$PWD/.venv/bin/python3" ]; then
    PYTHON_BIN="$PWD/.venv/bin/python3"
fi
exec "$PYTHON_BIN" -m uvicorn echoledger.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" --workers 1 --no-access-log
