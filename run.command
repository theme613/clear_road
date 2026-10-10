#!/bin/bash
# One-step launcher: sets everything up if needed, then starts Clear Road.
cd "$(dirname "$0")" || exit 1

if [ ! -d .venv ]; then
  PY=""
  for p in python3.12 python3.11 python3.13 python3.10 python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
      PY="$p"; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "Python 3.10+ not found. Install it with: brew install python@3.12"
    exit 1
  fi
  echo "[run] creating .venv with $PY"
  "$PY" -m venv .venv || exit 1
  .venv/bin/pip install -q -r requirements.txt || exit 1
fi

if [ ! -f model/model.joblib ] || [ ! -f data/trips.csv ]; then
  echo "[run] building data and model (first time only)"
  .venv/bin/python ml/pull_rain.py && .venv/bin/python ml/simulate.py && .venv/bin/python ml/train.py || exit 1
fi

if [ ! -f data/rail.json ]; then
  echo "[run] downloading the rail network (first time only)"
  .venv/bin/python ml/build_rail.py
fi

if [ ! -f model/congestion.joblib ]; then
  echo "[run] training the traffic model (first time only)"
  [ -f data/rain_history.csv ] || .venv/bin/python ml/pull_rain.py
  .venv/bin/python ml/simulate_congestion.py && .venv/bin/python ml/train_congestion.py || exit 1
fi

echo "[run] Clear Road running at http://localhost:8000  (Ctrl+C to stop)"
( sleep 2; open http://localhost:8000 ) >/dev/null 2>&1 &
exec .venv/bin/uvicorn app.main:app --port 8000
