#!/bin/zsh
# One-shot local wrapper for the independently staged, 2026-09-09 acquisition.
# It has no publication or postprocess operation: those remain explicit gates.
set -euo pipefail
cd /Users/luke808/ASL
exec /Users/luke808/ASL/.venv/bin/python /Users/luke808/ASL/tools/run_daily_facts_phase1_full_market.py --data-root /Users/luke808/AI/local-a-share-data-service-data --run-name daily_facts_phase1_20260909_v01 --start 2026-09-09 --end 2026-09-09 --acquire-only
