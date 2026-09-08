#!/bin/zsh
# One-shot local wrapper for the independently staged full acquisition.
set -euo pipefail
cd /Users/luke808/ASL
exec /Users/luke808/ASL/.venv/bin/python /Users/luke808/ASL/tools/run_daily_facts_phase1_full_market.py --data-root /Users/luke808/AI/local-a-share-data-service-data
