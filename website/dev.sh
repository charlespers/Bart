#!/usr/bin/env bash
# website dev server — bootstraps a venv and starts uvicorn on :3000.
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv"
REQ_HASH_FILE="$VENV/.reqs_hash"
REQ_HASH=$(shasum -a 256 requirements.txt 2>/dev/null | cut -d' ' -f1 || echo "missing")

if [ ! -d "$VENV" ]; then
    echo "  → creating website venv"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip wheel
    "$VENV/bin/pip" install --quiet -r requirements.txt
    echo "$REQ_HASH" > "$REQ_HASH_FILE"
elif [ ! -f "$REQ_HASH_FILE" ] || [ "$(cat "$REQ_HASH_FILE" 2>/dev/null)" != "$REQ_HASH" ]; then
    echo "  → updating website deps"
    "$VENV/bin/pip" install --quiet -r requirements.txt
    echo "$REQ_HASH" > "$REQ_HASH_FILE"
fi

exec "$VENV/bin/python" -m uvicorn server:app \
    --host 127.0.0.1 --port 3000 --reload
