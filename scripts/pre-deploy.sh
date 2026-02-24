#!/bin/bash
# Pre-deploy validation — run BEFORE restarting polymarket-bot
# Exit code 0 = safe to deploy, non-zero = abort
set -e

cd "$(dirname "$0")/.."

echo "🔍 Running smoke tests..."
source polymarket-venv/bin/activate
python -m pytest bot/tests/test_smoke.py -v --tb=short 2>&1

echo ""
echo "✅ All pre-deploy checks passed"
