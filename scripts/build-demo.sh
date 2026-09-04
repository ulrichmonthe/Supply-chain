#!/usr/bin/env bash
#
# Build the static demo published at docs/app.
#
# The tool needs a Python backend and a MILP solver, which GitHub Pages cannot
# run. So every answer the interface can ask for is solved ahead of time and
# written next to it as JSON, and a small shim points fetch at those files.
#
# The interface itself is the unmodified production build. Only the data source
# changes.
#
# Usage:  make demo         (starts the API itself)
#         bash scripts/build-demo.sh   (expects the API already on :8000)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API="${API:-http://127.0.0.1:8000/api}"
OUT="$ROOT/docs/app"

command -v npm >/dev/null 2>&1 || { echo "npm not found. The demo build needs Node."; exit 1; }
curl -sf -o /dev/null "$API/countries" || {
  echo "No API on $API. Start it with 'make run' in another terminal first."; exit 1; }

echo "==> solving every scenario and month"
"$ROOT/.venv/bin/python" "$ROOT/scripts/capture_demo.py" "$OUT/api"

echo "==> capturing the spreadsheet exports"
mkdir -p "$OUT/api/files"
curl -sf "$API/template.xlsx" -o "$OUT/api/files/template.xlsx"
curl -sf "$API/countries/1/export/network.xlsx" -o "$OUT/api/files/network.xlsx"
for sid in $(curl -sf "$API/countries/1/scenarios" | python3 -c \
      'import json,sys; print(" ".join(str(s["id"]) for s in json.load(sys.stdin) if s.get("latest_result_id")))'); do
  curl -sf "$API/scenarios/$sid/export/results.xlsx" -o "$OUT/api/files/results-$sid.xlsx" || true
done

echo "==> building the interface with relative asset paths"
# --base=./ matters: the demo is served from a subdirectory, not a domain root.
( cd "$ROOT/frontend" && npm run build -- --base=./ )

echo "==> assembling $OUT"
cp -R "$ROOT/frontend/dist/." "$OUT/"
cp "$ROOT/frontend/demo/demo-api.js" "$OUT/demo-api.js"

# The shim has to be installed before the app's own bundle runs.
python3 - "$OUT/index.html" <<'PY'
import sys, pathlib, re
p = pathlib.Path(sys.argv[1]); s = p.read_text()
tag = '<script src="./demo-api.js"></script>'
if tag not in s:
    s = re.sub(r'(<script type="module")', tag + r'\n    \1', s, count=1)
    p.write_text(s)
    print("   shim injected into index.html")
else:
    print("   shim already present")
PY

echo
echo "Demo built. Preview it with:"
echo "  (cd $OUT && python3 -m http.server 4400)  then open http://localhost:4400/"
