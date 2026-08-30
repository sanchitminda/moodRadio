#!/usr/bin/env bash
# Run Mood Radio locally (no Docker), with --reload for live editing.
# Reuses your .env for credentials but translates the Docker-only values
# (container DB path + host.docker.internal) to local equivalents.
set -euo pipefail
cd "$(dirname "$0")"

# --- pick a python ----------------------------------------------------------
if command -v python >/dev/null 2>&1; then PYBOOT=python
elif command -v py >/dev/null 2>&1; then PYBOOT=py
else echo "No 'python' found on PATH."; exit 1; fi

# --- venv -------------------------------------------------------------------
VENV=.venv
if [ ! -d "$VENV" ]; then
  echo "Creating virtualenv in $VENV ..."
  "$PYBOOT" -m venv "$VENV"
fi
if [ -x "$VENV/Scripts/python.exe" ]; then PY="$VENV/Scripts/python.exe"   # Windows
else PY="$VENV/bin/python"; fi                                            # *nix/mac

# --- deps -------------------------------------------------------------------
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r requirements.txt

# --- Docker -> local overrides (the rest of .env is loaded by config.py) ----
export DB_PATH="./moodradio.db"
PORT=8080
if [ -f .env ]; then
  nav=$(grep -E '^[[:space:]]*NAVIDROME_URL=' .env | tail -1 | cut -d= -f2- | tr -d '\r'  | sed 's#host\.docker\.internal#localhost#')
  llm=$(grep -E '^[[:space:]]*LLM_BASE_URL='  .env | tail -1 | cut -d= -f2- | tr -d '\r'  | sed 's#host\.docker\.internal#localhost#')
  prt=$(grep -E '^[[:space:]]*PORT='          .env | tail -1 | cut -d= -f2- | tr -d '\r ' )
  [ -n "$nav" ] && export NAVIDROME_URL="$nav"
  [ -n "$llm" ] && export LLM_BASE_URL="$llm"
  [ -n "$prt" ] && PORT="$prt"
fi

echo "→ NAVIDROME_URL=${NAVIDROME_URL:-<from .env>}"
echo "→ LLM_BASE_URL=${LLM_BASE_URL:-<from .env>}"
echo "→ DB_PATH=$DB_PATH   (SQLite in the project folder)"
echo "Starting on http://localhost:$PORT   (Ctrl+C to stop)"
exec "$PY" -m uvicorn app.main:app --reload --port "$PORT"
