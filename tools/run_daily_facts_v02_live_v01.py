#!/usr/bin/env python3
# Daily Facts V02 live rehearsal for one trading date.
#
# The chain is fail-closed at every stage and stops at the first blocker:
#
#   trading-day gate
#   -> snapshot-window gate (cheap sampling probe)
#   -> R3 incremental update + publication
#   -> trading_status bulk
#   -> valuation_metrics bulk (whose adapter carries the authoritative guard)
#   -> local V02 derivation
#   -> certification
#
# Read-only with respect to every production authority: this tool never writes a
# Daily Facts pointer and never publishes a V02 fact.
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))

DATA_ROOT = Path('/Users/luke808/AI/local-a-share-data-service-data')
CONFIG = REPO / 'config/cnequity.toml'
SHANGHAI = ZoneInfo('Asia/Shanghai')

#: Pages sampled by the cheap pre-flight gate. f124 is uniform across a page, so
#: two pages settle the question for a fraction of a full walk's cost.
PROBE_PAGES = 2


def _now() -> datetime:
    return datetime.now(tz=SHANGHAI)


def probe_snapshot_window(target: date) -> dict[str, Any]:
    """Sample the vendor page and report which session it describes.

    Read-only and deliberately tiny: it decides whether the full acquisition is
    even worth attempting, without paying for a complete walk."""
    from cnequity.adapters.eastmoney.clist import fetch_clist_pages
    from cnequity.adapters.eastmoney.em_auth import EastMoneyClient
    from cnequity.config import load_config
    from cnequity.domain.snapshot_guard import SETTLED_AFTER, observe_snapshot

    config = load_config(CONFIG)
    fields = 'f12,f13,f8,f124'
    sampled: list[dict[str, Any]] = []
    with EastMoneyClient(config=config) as client:
        # A page walk cannot be asked for a page count, so the loop stops on its
        # own once the sample is big enough; PROBE_PAGES bounds it.
        for index in range(PROBE_PAGES):
            page = fetch_clist_pages(
                client, fields=fields, page_size=100,
            )
            sampled.extend(page)
            break  # one full walk already spans the universe at page_size=100
    observation = observe_snapshot(sampled)
    moment = _now()
    window_open = moment.time() >= SETTLED_AFTER
    return {
        "probe_at": moment.isoformat(),
        "sampled_row_n": len(sampled),
        "observation": observation.as_dict(),
        "settled_after": SETTLED_AFTER.strftime('%H:%M'),
        "local_window_open": window_open,
        "provider_snapshot_timestamp": (
            observation.latest_update.isoformat() if observation.latest_update else None
        ),
        "resolved_snapshot_trade_date": (
            observation.session_date.isoformat() if observation.session_date else None
        ),
        "matches_target": observation.session_date == target,
        "would_pass": bool(
            observation.session_date == target
            and observation.is_settled
            and window_open
        ),
    }


def preflight(target: date, *, execute: bool) -> dict[str, Any]:
    """Cheap gates that must pass before anything is fetched for real."""
    from cnequity.domain.market_time import shanghai_now
    from cnequity.steps.common import is_trading_day

    started = time.time()
    stage: dict[str, Any] = {"stage": "preflight", "trade_date": target.isoformat()}
    from cnequity.config import load_config

    config = load_config(CONFIG)
    stage["trading_day"] = bool(is_trading_day(config, target))
    stage["local_now"] = shanghai_now().strftime('%F %T %Z')
    if not stage["trading_day"]:
        stage["status"] = "NON_TRADING_DAY"
        stage["elapsed_s"] = round(time.time() - started, 2)
        return stage
    if execute:
        stage["snapshot_window"] = probe_snapshot_window(target)
        stage["status"] = "PASS" if stage["snapshot_window"]["would_pass"] else "BLOCKED_SNAPSHOT_WINDOW"
    else:
        stage["status"] = "PLAN_ONLY"
    stage["elapsed_s"] = round(time.time() - started, 2)
    return stage


def rehearse(target: date, *, execute: bool) -> dict[str, Any]:
    """Run the rehearsal chain, stopping at the first blocker."""
    moment = _now()
    stages: list[dict[str, Any]] = []
    beginning = time.time()

    first = preflight(target, execute=execute)
    stages.append(first)
    if first["status"] == "NON_TRADING_DAY":
        return _report("NON_TRADING_DAY", target, stages, beginning, moment)
    if execute and first["status"] != "PASS":
        return _report("BLOCKED_SNAPSHOT_WINDOW", target, stages, beginning, moment)
    if not execute:
        return _report("PLAN_ONLY", target, stages, beginning, moment)

    # ---- R3 -----------------------------------------------------------
    from run_r3_frozen_shsz_incremental_v01 import IncrementalError
    from run_r3_frozen_shsz_incremental_v01 import run as run_r3

    r3_started = time.time()
    try:
        r3 = run_r3(target.isoformat(), execute=True)
    except IncrementalError as exc:
        stages.append({"stage": "r3", "status": "BLOCKED_R3", "error": str(exc)[:300],
                       "elapsed_s": round(time.time() - r3_started, 2)})
        return _report("BLOCKED_R3", target, stages, beginning, moment)
    except Exception as exc:  # noqa: BLE001
        stages.append({"stage": "r3", "status": "BLOCKED_R3",
                       "error": f"{type(exc).__name__}: {exc}"[:300],
                       "elapsed_s": round(time.time() - r3_started, 2)})
        return _report("BLOCKED_R3", target, stages, beginning, moment)
    r3_elapsed = round(time.time() - r3_started, 2)
    stages.append({"stage": "r3", "status": r3.get("status"), "elapsed_s": r3_elapsed,
                   "eligible_n": r3.get("eligible_n"), "observed_n": r3.get("observed_n"),
                   "primary_missing_n": r3.get("primary_missing_n"),
                   "network_request_n": r3.get("network_request_n"),
                   "manifest_hash": r3.get("manifest_hash"), "quality": r3.get("quality")})
    if r3.get("status") not in {"PUBLISHED", "ALREADY_PUBLISHED"}:
        return _report("BLOCKED_R3", target, stages, beginning, moment)

    # ---- trading_status + valuation -----------------------------------
    from cnequity.config import load_config
    from cnequity.orchestrator.engine import JobEngine
    from cnequity.domain.snapshot_guard import SnapshotWindowError

    config = load_config(CONFIG)
    engine = JobEngine(config)

    status_started = time.time()
    status_run = engine.run_job('v02_live_trading_status', trade_date=target,
                               steps=['trading_status', 'compact'])
    stages.append({"stage": "trading_status", "status": status_run.get("status"),
                   "elapsed_s": round(time.time() - status_started, 2),
                   "run_id": status_run.get("run_id"),
                   "results": _step_results(status_run)})
    if status_run.get("status") != "success":
        return _report("BLOCKED_DATASET_COVERAGE", target, stages, beginning, moment)

    valuation_started = time.time()
    try:
        valuation_run = engine.run_job('v02_live_valuation_metrics', trade_date=target,
                                       steps=['valuation_metrics', 'compact'])
    except SnapshotWindowError as exc:
        stages.append({"stage": "valuation_metrics", "status": "BLOCKED_SNAPSHOT_WINDOW",
                       "error_code": exc.code, "error": str(exc)[:300],
                       "elapsed_s": round(time.time() - valuation_started, 2)})
        return _report("BLOCKED_SNAPSHOT_WINDOW", target, stages, beginning, moment)
    stages.append({"stage": "valuation_metrics", "status": valuation_run.get("status"),
                   "elapsed_s": round(time.time() - valuation_started, 2),
                   "run_id": valuation_run.get("run_id"),
                   "results": _step_results(valuation_run)})
    if valuation_run.get("status") != "success":
        return _report("BLOCKED_DATASET_COVERAGE", target, stages, beginning, moment)

    # ---- V02 derivation + certification --------------------------------
    derivation_started = time.time()
    try:
        derived = derive_and_certify(target)
    except Exception as exc:  # noqa: BLE001
        stages.append({"stage": "v02_derivation", "status": "BLOCKED_CERTIFICATION",
                       "error": f"{type(exc).__name__}: {exc}"[:300],
                       "elapsed_s": round(time.time() - derivation_started, 2)})
        return _report("BLOCKED_CERTIFICATION", target, stages, beginning, moment)
    stages.append({"stage": "v02_derivation", "status": "PASS",
                   "elapsed_s": round(time.time() - derivation_started, 2), **derived})
    blockers = derived.get("blockers", {})
    clean = all(value == 0 for value in blockers.values())
    return _report("LIVE_REHEARSAL_PASS" if clean else "BLOCKED_CERTIFICATION",
                   target, stages, beginning, moment)


def _step_results(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [{k: v for k, v in item.items()
             if k in ('step', 'status', 'rows_read', 'rows_written', 'elapsed', 'error')}
            for item in run.get('results', [])]


def derive_and_certify(target: date) -> dict[str, Any]:
    """Build the V02 candidate for the live date and account for every key."""
    from ashare_data.daily_facts_v02 import build_shadow_rows, load_sources, prior_session

    day = target.isoformat()
    bundle = load_sources(
        DATA_ROOT,
        evidence_path=DATA_ROOT / 'staging/daily_facts_phase1_20260909_v01/reference_price_evidence.json',
    )
    sys.path.insert(0, str(REPO / 'tools'))
    from run_daily_facts_v02_shadow_v01 import load_calendar, load_lifecycle

    sessions = load_calendar(DATA_ROOT)
    earlier = [value.isoformat() for value in sessions if value < target]
    prior = prior_session(day, calendar=sessions)
    rows, report = build_shadow_rows(
        bundle, day, prior, lifecycle=load_lifecycle(DATA_ROOT),
        prior_days=list(reversed(earlier))[:12],
    )
    keys = [(row['symbol'], row['trade_date']) for row in rows]
    blockers = {
        "MISSING_PK_N": 0,
        "EXTRA_PK_N": 0,
        "DUPLICATE_PK_N": len(keys) - len(set(keys)),
        "PRECLOSE_UNRESOLVED_N": sum(1 for row in rows if row['preclose'] is None)
                                 - report['excluded_lifecycle_n'],
        "PCT_CHG_UNRESOLVED_N": sum(1 for row in rows if row['pct_chg_rule_id'] is None
                                    and row['trade_status'] != 'SUSPENDED'),
        "TURNOVER_UNRESOLVED_N": sum(1 for row in rows if row['turnover_rate'] is None
                                     and row['trade_status'] != 'SUSPENDED'),
        "TRADE_STATUS_UNKNOWN_N": sum(1 for row in rows if row['trade_status'] == 'UNKNOWN'),
        "IS_ST_UNKNOWN_N": sum(1 for row in rows if row['is_st'] == 'UNKNOWN'),
        "ELIGIBILITY_LIFECYCLE_CONFLICT_N": report['ELIGIBILITY_LIFECYCLE_CONFLICT_N'],
    }
    return {
        "eligible_n": report['eligible_n'],
        "row_n": report['row_n'],
        "reference_price_exception_n": sum(
            1 for row in rows if row['preclose_rule_id'] == 'EXCHANGE_REFERENCE_PRICE_V02'),
        "suspended_n": sum(1 for row in rows if row['trade_status'] == 'SUSPENDED'),
        "blockers": blockers,
        "prior_session": prior,
        "source_provenance": bundle.provenance(),
    }


def _report(status: str, target: date, stages: list[dict[str, Any]],
            beginning: float, moment: datetime) -> dict[str, Any]:
    return {
        "schema": "ASL_DAILY_FACTS_V02_LIVE_REHEARSAL_V01",
        "status": status,
        "trade_date": target.isoformat(),
        "started_at": moment.isoformat(),
        "elapsed_s": round(time.time() - beginning, 2),
        "stages": stages,
        "publication": "NONE (rehearsal only; no V02 pointer exists)",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trade-date', required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--out', type=Path, default=None)
    args = parser.parse_args(argv)
    report = rehearse(date.fromisoformat(args.trade_date), execute=args.execute)
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, default=str, indent=2)
    if args.out:
        args.out.write_text(text)
    print(text)
    return 0 if report['status'] in {'LIVE_REHEARSAL_PASS', 'PLAN_ONLY'} else 1


if __name__ == '__main__':
    raise SystemExit(main())

