#!/usr/bin/env bash
# Start PDF Studio. Usage: ./run.sh [port]
set -euo pipefail
cd "$(dirname "$0")"

python3 - <<'PY' || pip install -r requirements.txt
import importlib, sys
need = ["fastapi", "uvicorn", "fitz", "docx", "PIL", "numpy"]
missing = [m for m in need if importlib.util.find_spec(m) is None]
print("missing:", missing)
sys.exit(1 if missing else 0)
PY

PORT="${1:-8000}"
echo "PDF Studio -> http://127.0.0.1:${PORT}"
PORT="$PORT" python3 server.py
