#!/bin/zsh
# One command to run the coach: ./run.sh   (then open http://localhost:8765)
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv && .venv/bin/pip install -q chess requests flask
fi
command -v stockfish >/dev/null || brew install stockfish
[ -f report.json ] || .venv/bin/python sync.py
# already running? just open it
if curl -s -o /dev/null -w "%{http_code}" http://localhost:8765/api/sync 2>/dev/null | grep -q 200; then
  echo "Chess coach is already running at http://localhost:8765"
  open http://localhost:8765; exit 0
fi
(sleep 1.5 && open http://localhost:8765) &
exec .venv/bin/python app.py
