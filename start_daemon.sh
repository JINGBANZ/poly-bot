#!/bin/bash
set -euo pipefail
cd /home/ubuntu/.openclaw/workspace/polymarket-bot
source polymarket-venv/bin/activate
exec python -m bot.main
