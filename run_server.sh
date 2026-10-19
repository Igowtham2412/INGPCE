#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
    for candidate in python3.11 python3.10 python3.12 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            candidate_version=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
            case "$candidate_version" in
                3.10|3.11|3.12) PYTHON_BIN="$candidate"; break ;;
            esac
        fi
    done
fi
if [ -z "$PYTHON_BIN" ] || ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    printf 'Python 3.10-3.12 was not found. Install a supported version or set PYTHON_BIN.\n' >&2
    exit 1
fi
PYTHON_VERSION=$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
case "$PYTHON_VERSION" in
    3.10|3.11|3.12) ;;
    *) printf 'Python %s is unsupported. Install Python 3.10, 3.11, or 3.12.\n' "$PYTHON_VERSION" >&2; exit 1 ;;
esac

if [ ! -d .venv ]; then
    "$PYTHON_BIN" -m venv .venv
fi

.venv/bin/python -m pip install -r requirements.txt

if [ ! -f "${PPE_MODEL_PATH:-models/best.pt}" ]; then
    .venv/bin/python scripts/download_model.py --out "${PPE_MODEL_PATH:-models/best.pt}"
fi

exec .venv/bin/python -m app.main