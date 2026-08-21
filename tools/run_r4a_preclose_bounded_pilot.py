#!/usr/bin/env python3
"""Bounded R4A preclose adapter pilot (dry-run by default; real exec forbidden).

The bounded adapter builds the formal preclose_facts row contract for the
frozen R4A3 24-symbol pilot from a BaoStock query_history_k_data_plus response
under the hardened V01.1 authority. This task is minimal correctness patch +
offline regression + read-only real-root dry-run only: provider fetch is
forbidden, real --exec is forbidden, and no preclose market dataset is written.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ashare_data.r4a_preclose_bounded_adapter import (  # noqa: E402
    AS_OF,
    CNEQUITY_PIN,
    DATA_ROOT_DEFAULT,
    EXPECTED_ADAPTER_SHA,
    WINDOW_START,
    build_query_plan,
    load_frozen_sentinel_evidence,
    load_pilot_symbols,
    r4a0_prerequisite,
)

OUTPUT_DIR = REPO_ROOT / "reports" / "implementation"
REPORT_PATH = OUTPUT_DIR / "R4A5_PRECLOSE_BOUNDED_ADAPTER_V01_1.md"


def _render_report(result: dict[str, Any]) -> str:
    lines = [
        "# R4A5.1 PRECLOSE BOUNDED ADAPTER HARDENING — V01_1 (author report)",
        "",
        "DATE: 2026-08-21",
        "BRANCH: codex/r4a5-1-preclose-adapter-hardening-v01",
        f"CONTRACT_HEAD: {result['CONTRACT_HEAD']}",
        f"AS_OF: {AS_OF.isoformat()}",
        f"PINNED_CNEquity: {CNEQUITY_PIN}",
        "",
        f"## ADAPTER_STATUS={result.get('ADAPTER_STATUS')}",
        "",
        "## R4A0 PREREQUISITE (independent fields)",
        "",
        f"R4A0_READY={result.get('R4A0_READY')}   (r4a0 run_gate)",
        f"R3_IDENTITY_MATCH={result.get('R3_IDENTITY_MATCH')}   (independent)",
        f"FORMAL_IDENTITY_N={result.get('FORMAL_IDENTITY_N')}",
        f"FORMAL_IDENTITY_HASH={result.get('FORMAL_IDENTITY_HASH')}",
        "",
        "## DRY_RUN",
        "",
        f"DRY_RUN_STATUS={result.get('DRY_RUN_STATUS')}",
        f"PILOT_SYMBOL_N={result.get('PILOT_SYMBOL_N')}",
        f"PILOT_SYMBOL_HASH={result.get('PILOT_SYMBOL_HASH')}",
        f"QUERY_WINDOW_N={result.get('QUERY_WINDOW_N')}",
        f"QUERY_PLAN_HASH={result.get('QUERY_PLAN_HASH')}",
        f"NETWORK_PROVIDER_DATA_FETCH={result.get('NETWORK_PROVIDER_DATA_FETCH')}",
        f"MARKET_DATA_WRITE={result.get('MARKET_DATA_WRITE')}",
        "",
        "## OFFICIAL SENTINELS (expected contract; runtime NOT_RUN in dry-run)",
        "",
        f"FROZEN_SENTINEL_EXPECTED_N={result.get('FROZEN_SENTINEL_EXPECTED_N')}",
        f"FROZEN_OFFICIAL_SENTINEL_RUNTIME_STATUS={result.get('FROZEN_OFFICIAL_SENTINEL_RUNTIME_STATUS')}",
        "",
        "## QUERY PLAN HASH CONTRACT",
        "",
        "QUERY_PLAN_HASH covers symbol, bs_code, year, start, end, fields,",
        "frequency, adjustflag, source_version, query_contract_version, AS_OF,",
        "WINDOW_START, CNEQUITY_PIN. Same executable contract -> same hash.",
        "",
        "## QUALITY_GATE_CONTRACT",
        "",
        "QUALITY_GATE_PASS=true only if FORMAL_FACT_ROW_N==REQUIRED_ROW_N AND",
        "MISSING_REQUIRED_N=0 AND UNEXPECTED_TRADED_N=0 AND TRADESTATUS_UNKNOWN_N=0",
        "AND IDENTITY_FAILURE_N=0 AND WINDOW_SCOPE_FAILURE_N=0 AND DUPLICATE_N=0",
        "AND POST_ASOF_N=0 AND INVALID_PRECLOSE_N=0 AND every formal row has",
        "provider_tradestatus=1, finite positive preclose, coverage_status=COVERED.",
        "PROVIDER_SUSPENDED_SUPERSET_N may be >0 (non-blocking).",
        "",
        "## FORMAL_ROW_CONTRACT",
        "",
        "formal rows = required key AND tradestatus=='1' AND preclose finite",
        "positive AND provider code/date exact AND trade_date<=AS_OF;",
        "source=BAOSTOCK_HISTORY_K_PRECLOSE, source_version=baostock-0.9.3,",
        "query_contract_version=R4A_PRECLOSE_V01, provider_tradestatus=1,",
        "coverage_status=COVERED. Audit rows never enter preclose_facts.",
        "adapter_version requires expected+runtime SHA match in real execution.",
        "",
        "## WRITE_BOUNDARY",
        "",
        "FORMAL_PRECLOSE_DATASET_WRITE=NO",
        "MARKET_DATA_WRITE=NO",
        "MANIFEST_MUTATION=NO",
        "REAL_ROOT_ACCESS=READ_ONLY",
        "PROVIDER_DATA_FETCH=NO",
        "",
        "## KNOWN_UNIMPLEMENTED",
        "",
        "full resumable orchestrator / full 5456 extraction / PRECLOSE_COMPLETE",
        "promotion / R4B / turnover / isST / shared provider extraction.",
        "",
        "## BOUNDED_NEXT_ACTION",
        "",
        "Sol independent audit of the exact pushed commit; upon code-audit",
        "approval, a separate bounded real pilot may reuse this adapter with a",
        "real BaoStock session (provider fetch) under the hardened gates.",
    ]
    return "\n".join(lines) + "\n"


def _git_head() -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "UNKNOWN_HEAD"


def main() -> int:
    parser = argparse.ArgumentParser(description="bounded R4A preclose adapter pilot")
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--report", action="store_true", help="write small author report")
    parser.add_argument(
        "--exec",
        action="store_true",
        help="FORBIDDEN in this task: real provider execution is not authorized",
    )
    args = parser.parse_args()

    if args.exec:
        print("ERROR: real --exec is FORBIDDEN until Sol code audit; dry-run only", file=sys.stderr)
        return 3

    try:
        pilot = load_pilot_symbols(REPO_ROOT)
        prereq = r4a0_prerequisite(args.data_root)
        plan = build_query_plan(pilot["pilot_symbols"], window_start=WINDOW_START, as_of=AS_OF)
        sentinels = load_frozen_sentinel_evidence(REPO_ROOT)
        sentinel_expected = len(sentinels)
    except Exception as exc:  # noqa: BLE001 - CLI surface
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    result: dict[str, Any] = {
        "CONTRACT_HEAD": "e0b6c9325c2a2951c4c51dff4c2ee2332115d48c",
        "BRANCH": "codex/r4a5-1-preclose-adapter-hardening-v01",
        "ADAPTER_STATUS": "IMPLEMENTED_DRY_RUN_ONLY",
        "R4A0_READY": prereq["R4A0_READY"],
        "R3_IDENTITY_MATCH": prereq["R3_IDENTITY_MATCH"],
        "FORMAL_IDENTITY_N": prereq["FORMAL_IDENTITY_N"],
        "FORMAL_IDENTITY_HASH": prereq["FORMAL_IDENTITY_HASH"],
        "PILOT_SYMBOL_N": len(pilot["pilot_symbols"]),
        "PILOT_SYMBOL_HASH": pilot["pilot_symbol_hash"],
        "QUERY_WINDOW_N": plan["QUERY_WINDOW_N"],
        "QUERY_PLAN_HASH": plan["QUERY_PLAN_HASH"],
        "EXPECTED_ADAPTER_SHA": EXPECTED_ADAPTER_SHA,
        "RUNTIME_HEAD": _git_head(),
        "FROZEN_SENTINEL_EXPECTED_N": sentinel_expected,
    }
    if not prereq["R4A0_READY"]:
        result["DRY_RUN_STATUS"] = "BLOCKED"
        result["FROZEN_OFFICIAL_SENTINEL_RUNTIME_STATUS"] = "NOT_RUN_DRY_RUN"
        result["NETWORK_PROVIDER_DATA_FETCH"] = "NO"
        result["MARKET_DATA_WRITE"] = "NO"
    else:
        result["DRY_RUN_STATUS"] = "OK"
        result["FROZEN_OFFICIAL_SENTINEL_RUNTIME_STATUS"] = "NOT_RUN_DRY_RUN"
        result["NETWORK_PROVIDER_DATA_FETCH"] = "NO"
        result["MARKET_DATA_WRITE"] = "NO"

    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.report:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(_render_report(result), encoding="utf-8")
        print(f"\nreport written: {REPORT_PATH}")
    return 1 if not prereq["R4A0_READY"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
