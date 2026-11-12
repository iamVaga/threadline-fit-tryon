#!/bin/sh
# Starts FitCheck. With a filled-in .env it uses the real try-on model; without one it runs in mock mode.
cd "$(dirname "$0")"
pkill -f "uvicorn server:app" 2>/dev/null
if [ -f .env ]; then set -a; . ./.env; set +a; else export MOCK=1; echo "No .env found: running in MOCK mode"; fi
exec .venv/bin/uvicorn server:app --port 7860
