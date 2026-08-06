#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# docs/build_local.sh
#
# Build the documentation site locally and serve it for viewing.
# Runs docs/build.py (Sphinx/Furo narrative + autodoc API -> ./gh-pages), then
# starts a local web server.
#
# Usage:
#   docs/build_local.sh                 # build, then serve at http://localhost:8000/
#   docs/build_local.sh --port 9000     # serve on a different port
#   docs/build_local.sh --no-serve      # just build gh-pages/, don't start a server
#
# Requirements: python3 (the Sphinx toolchain is pip-installed by docs/build.py
# from docs/requirements.txt).
# ---------------------------------------------------------------------------
set -euo pipefail

PORT=8000
SERVE=1

while [ $# -gt 0 ]; do
  case "$1" in
    --no-serve) SERVE=0 ;;
    --port) PORT="$2"; shift ;;
    --port=*) PORT="${1#*=}" ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

command -v python3 >/dev/null || { echo "ERROR: 'python3' not found in PATH." >&2; exit 1; }

echo "==> Building documentation (docs/build.py)…"
python3 docs/build.py

echo "==> Done. Site is in: $ROOT/gh-pages"
if [ "$SERVE" -eq 0 ]; then
  echo "    Open gh-pages/index.html, or serve with: python3 -m http.server --directory gh-pages"
  exit 0
fi

echo
echo "    Documentation : http://localhost:$PORT/"
echo "    (Press Ctrl-C to stop the server.)"
echo

exec python3 -m http.server "$PORT" --directory gh-pages
