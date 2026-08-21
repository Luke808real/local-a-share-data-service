"""R4A bounded BaoStock preclose adapter (implementation stage, V01).

Frozen contract authority:
  docs/plans/R4A_PRECLOSE_CANONICAL_SOURCE_CONTRACT_V01.md (V01.1)

This module builds the formal preclose_facts rows for a bounded symbol set
from a BaoStock query_history_k_data_plus response (fields
date,code,preclose,tradestatus; frequency d; adjustflag 3), strictly
normalizing every provider row and failing closed. It never fetches from the
provider itself and never writes market data; all results are in-memory and
offline tests use a fake provider.

The real BaoStock login/query capability is deliberately not executed in this
task. BaostockSessionProvider is the thin production wrapper that reuses
BaoStock login/query_history_k_data_plus; it exists only as the future real
path and is never called here (dry-run and tests do not enter it).
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from decimal import ROUND_HALF_UP
from decimal import Decimal
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Iterable
from typing import Protocol

import polars as pl


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")

WINDOW_START = date(2016, 1, 1)
AS_OF = date(2026, 8, 17)

FORMAL_IDENTITY_N = 5456
FORMAL_IDENTITY_HASH = "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"
CNEQUITY_PIN = "a18ee0484dfb0801650175471724def3228b8a17"

SOURCE = "BAOSTOCK_HISTORY_K_PRECLOSE"
SOURCE_VERSION = "baostock-0.9.3"
QUERY_CONTRACT_VERSION = "R4A_PRECLOSE_V01"
QUERY_FIELDS = "date,code,preclose,tradestatus"
QUERY_FREQUENCY = "d"
QUERY_ADJUSTFLAG = "3"

PILOT_MANIFEST_REL = Path(
    "reports/research/R4A3_BAOSTOCK_PRECLOSE_PILOT_SYMBOL_MANIFEST.json"
)
PILOT_MANIFEST_HASH = "5fa9f5c9ef376f0c453d3f543dc3a8ee9d61f73cec3a0fd35a9bea5081e17843"
CLOSURE_RECEIPT_REL = Path(
    "reports/research/R4A3_1_BAOSTOCK_PRECLOSE_SOURCE_CLOSURE_RECEIPT.json"
)


def bs_code(symbol: str) -> str:
    code, exchange = symbol.split(".")
    if exchange not in {"SH", "SZ"} or len(code) != 6 or not code.isdigit():
        raise ValueError(f"non-canonical SH/SZ symbol: {symbol}")
    return ("sh" if exchange == "SH" else "sz") + "." + code


def parse_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def symbol_hash(symbols: Iterable[str]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(set(symbols)), separators=(",", ":")).encode()
    ).hexdigest()


def display_equal(left: Any, right: Any) -> bool:
    """Exact equality at exchange display precision (0.01, ROUND_HALF_UP).

    This is not a +-0.01 tolerance: two values must be identical after the
    same deterministic display normalization. CLEAN_NORMAL parity PASS
    requires raw values to be display-exact; no mismatch window is allowed.
    """
    try:
        left_dec = Decimal(str(left)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        right_dec = Decimal(str(right)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (TypeError, ValueError, ArithmeticError):
        return False
    return left_dec == right_dec


class BaoStockPrecloseProvider(Protocol):
    """Provider contract for the bounded adapter.

    The real implementation (BaostockSessionProvider) is never invoked in this
    task. Tests inject a fake provider implementing this shape.
    """

    def query_history_k_data_plus(
        self,
        code: str,
        fields: str,
        start_date: str,
        end_date: str,
        frequency: str,
        adjustflag: str,
    ) -> Iterable[list[str]]: ...


class BaostockSessionProvider:
    """Thin production wrapper over the real BaoStock login/query capability.

    Present for the future real execution path only. It is intentionally never
    called in the bounded-adapter task (no provider fetch allowed) and would
    import BaoStock lazily on first use. Both login and query raise a hard
    NotImplementedError so this task cannot accidentally run a real fetch.
    """

    def login(self) -> str:
        raise NotImplementedError("real BaoStock login is FORBIDDEN in this task")

    def query_history_k_data_plus(
        self,
        code: str,
        fields: str,
        start_date: str,
        end_date: str,
        frequency: str,
        adjustflag: str,
    ) -> Iterable[list[str]]:
        raise NotImplementedError("real BaoStock query is FORBIDDEN in this task")


def load_pilot_symbols(repo_root: Path | None = None) -> dict[str, Any]:
    """Load the frozen R4A3 24-symbol pilot manifest (receipt, not code)."""
    root = (repo_root or REPO_ROOT).resolve()
    path = root / PILOT_MANIFEST_REL
    if not path.exists():
        raise FileNotFoundError(f"frozen pilot manifest missing: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot read frozen pilot manifest: {path}") from exc
    symbols = tuple(str(s) for s in manifest.get("pilot_symbols", []))
    recorded_hash = manifest.get("pilot_symbol_hash")
    recorded_n = int(manifest.get("pilot_symbol_n", 0))
    computed_hash = symbol_hash(symbols)
    if recorded_hash != PILOT_MANIFEST_HASH or computed_hash != PILOT_MANIFEST_HASH:
        raise ValueError("frozen pilot manifest symbol hash mismatch")
    if recorded_n != len(symbols) or len(symbols) == 0:
        raise ValueError("frozen pilot manifest symbol count mismatch")
    return {"pilot_symbols": symbols, "pilot_symbol_hash": computed_hash}


def load_required_keys(
    root: Path,
    symbols: Iterable[str],
    *,
    as_of: date = AS_OF,
    window_start: date = WINDOW_START,
) -> dict[str, Any]:
    """Load required actual-traded keys from real R3 authoritative daily_bars.

    PK is (symbol, trade_date). Scope is constrained to the explicit bounded
    symbol set and trade_date <= AS_OF. Detects duplicate required keys,
    identity scope mismatch (symbol outside the bounded set), and post-ASOF
    rows, all of which fail closed. The provider never defines the required
    universe.
    """
    symbol_list = sorted(set(symbols))
    all_bars = (
        pl.scan_parquet(str(root / "curated" / "daily_bars" / "**" / "*.parquet"))
        .select(["symbol", "trade_date"])
        .filter(pl.col("symbol").is_in(symbol_list))
        .collect()
        .with_columns(
            pl.col("symbol").cast(pl.String),
            pl.col("trade_date").cast(pl.Date),
        )
    )
    post_asof_n = int(
        all_bars.filter(pl.col("trade_date") > as_of).height
    )
    bars = all_bars.filter(
        (pl.col("trade_date") >= window_start) & (pl.col("trade_date") <= as_of)
    )
    duplicate_pk_n = int(
        bars.group_by(["symbol", "trade_date"])
        .len()
        .filter(pl.col("len") > 1)
        .height
    )
    bars = bars.unique(subset=["symbol", "trade_date"], keep="first")
    scope_mismatch = sorted(
        set(str(s) for s in bars.get_column("symbol").to_list()) - set(symbol_list)
    )
    required_keys = {
        (str(row["symbol"]), row["trade_date"])
        for row in bars.iter_rows(named=True)
    }
    return {
        "required_keys": required_keys,
        "required_row_n": len(required_keys),
        "duplicate_required_key_n": duplicate_pk_n,
        "identity_scope_mismatch": scope_mismatch,
        "identity_scope_mismatch_n": len(scope_mismatch),
        "post_asof_n": post_asof_n,
        "symbols": symbol_list,
    }


def normalize_baostock_preclose_rows(
    provider_rows: Iterable[dict[str, Any]],
    required_keys: set[tuple[str, date]],
    symbols: Iterable[str],
    *,
    as_of: date = AS_OF,
) -> dict[str, Any]:
    """Strictly classify provider rows into the frozen contract's bins.

    Output classifications: ELIGIBLE_REQUIRED_ROW, PROVIDER_SUSPENDED_SUPERSET,
    UNEXPECTED_TRADED, TRADESTATUS_UNKNOWN, IDENTITY_FAILURE, POST_ASOF,
    DUPLICATE.

    A formal candidate must be: required key, tradestatus == "1", preclose
    finite positive, provider code exact, provider date exact, <= AS_OF.
    """
    expected_code = {symbol: bs_code(symbol) for symbol in symbols}
    counts = {
        "ELIGIBLE_REQUIRED_ROW": 0,
        "PROVIDER_SUSPENDED_SUPERSET": 0,
        "UNEXPECTED_TRADED": 0,
        "TRADESTATUS_UNKNOWN": 0,
        "IDENTITY_FAILURE": 0,
        "POST_ASOF": 0,
        "DUPLICATE": 0,
        "INVALID_PRECLOSE": 0,
    }
    eligible: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    eligible_keys: set[tuple[str, date]] = set()

    for row in provider_rows:
        symbol = str(row.get("symbol", ""))
        raw_date = row.get("date")
        parsed_date = parse_date(raw_date)
        provider_code = row.get("code")
        tradestatus = row.get("tradestatus")
        preclose = parse_float(row.get("preclose"))

        if parsed_date is None or provider_code != expected_code.get(symbol):
            counts["IDENTITY_FAILURE"] += 1
            audit.append(
                {"symbol": symbol, "date": str(raw_date), "issue": "IDENTITY_FAILURE"}
            )
            continue
        key = (symbol, parsed_date)
        if parsed_date > as_of:
            counts["POST_ASOF"] += 1
            audit.append(
                {"symbol": symbol, "date": parsed_date.isoformat(), "issue": "POST_ASOF"}
            )
            continue
        ts = str(tradestatus).strip() if tradestatus is not None else None
        if ts not in {"0", "1"}:
            counts["TRADESTATUS_UNKNOWN"] += 1
            audit.append(
                {
                    "symbol": symbol,
                    "date": parsed_date.isoformat(),
                    "issue": "TRADESTATUS_UNKNOWN",
                }
            )
            continue
        if ts == "0":
            counts["PROVIDER_SUSPENDED_SUPERSET"] += 1
            audit.append(
                {
                    "symbol": symbol,
                    "date": parsed_date.isoformat(),
                    "issue": "PROVIDER_SUSPENDED_SUPERSET",
                }
            )
            continue
        # tradestatus == "1"
        if key not in required_keys:
            counts["UNEXPECTED_TRADED"] += 1
            audit.append(
                {"symbol": symbol, "date": parsed_date.isoformat(), "issue": "UNEXPECTED_TRADED"}
            )
            continue
        if preclose is None or not (math.isfinite(preclose) and preclose > 0):
            counts["INVALID_PRECLOSE"] += 1
            audit.append(
                {"symbol": symbol, "date": parsed_date.isoformat(), "issue": "INVALID_PRECLOSE"}
            )
            continue
        if key in eligible_keys:
            counts["DUPLICATE"] += 1
            audit.append(
                {"symbol": symbol, "date": parsed_date.isoformat(), "issue": "DUPLICATE"}
            )
            continue
        eligible_keys.add(key)
        eligible.append(
            {
                "symbol": symbol,
                "trade_date": parsed_date,
                "preclose": preclose,
                "provider_tradestatus": 1,
                "coverage_status": "COVERED",
            }
        )
        counts["ELIGIBLE_REQUIRED_ROW"] += 1

    missing_required_n = len(required_keys - eligible_keys)
    return {
        "counts": counts,
        "eligible_rows": eligible,
        "audit_rows": audit,
        "missing_required_n": missing_required_n,
    }


def build_formal_facts(
    eligible_rows: list[dict[str, Any]],
    *,
    adapter_version: str,
    fetched_at: str,
) -> list[dict[str, Any]]:
    return [
        {
            "symbol": row["symbol"],
            "trade_date": row["trade_date"],
            "preclose": float(row["preclose"]),
            "source": SOURCE,
            "source_version": SOURCE_VERSION,
            "adapter_version": adapter_version,
            "query_contract_version": QUERY_CONTRACT_VERSION,
            "fetched_at": fetched_at,
            "provider_tradestatus": 1,
            "coverage_status": "COVERED",
        }
        for row in eligible_rows
    ]


def quality_gate_pass(
    *,
    required_row_n: int,
    formal_fact_row_n: int,
    missing_required_n: int,
    counts: dict[str, int],
    formal_rows: list[dict[str, Any]],
) -> bool:
    if formal_fact_row_n != required_row_n:
        return False
    if missing_required_n != 0:
        return False
    for key in (
        "UNEXPECTED_TRADED",
        "TRADESTATUS_UNKNOWN",
        "IDENTITY_FAILURE",
        "DUPLICATE",
        "POST_ASOF",
        "INVALID_PRECLOSE",
    ):
        if counts.get(key, 0) != 0:
            return False
    return all(
        row.get("provider_tradestatus") == 1
        and isinstance(row.get("preclose"), (int, float))
        and math.isfinite(float(row["preclose"]))
        and float(row["preclose"]) > 0
        and row.get("coverage_status") == "COVERED"
        for row in formal_rows
    )


def build_query_plan(
    symbols: Iterable[str],
    *,
    window_start: date = WINDOW_START,
    as_of: date = AS_OF,
) -> dict[str, Any]:
    """Deterministic per (symbol, year) query plan over the frozen window."""
    symbol_list = sorted(set(symbols))
    plan: list[dict[str, Any]] = []
    for symbol in symbol_list:
        for year in range(window_start.year, as_of.year + 1):
            year_start = max(window_start, date(year, 1, 1))
            year_end = min(as_of, date(year, 12, 31))
            if year_start > year_end:
                continue
            plan.append(
                {
                    "symbol": symbol,
                    "bs_code": bs_code(symbol),
                    "year": year,
                    "start": year_start.isoformat(),
                    "end": year_end.isoformat(),
                    "query": {
                        "fields": QUERY_FIELDS,
                        "frequency": QUERY_FREQUENCY,
                        "adjustflag": QUERY_ADJUSTFLAG,
                    },
                }
            )
    plan_hash = hashlib.sha256(
        json.dumps(
            [[p["symbol"], p["year"], p["start"], p["end"]] for p in plan],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "query_plan": plan,
        "QUERY_WINDOW_N": len(plan),
        "QUERY_PLAN_HASH": plan_hash,
    }


def run_bounded_adapter(
    *,
    root: Path,
    symbols: Iterable[str],
    provider_fetch: Callable[[dict[str, Any]], list[dict[str, Any]]] | None,
    dry_run: bool,
    adapter_version: str,
    fetched_at: str,
    as_of: date = AS_OF,
    window_start: date = WINDOW_START,
) -> dict[str, Any]:
    """Run the bounded adapter (in-memory) for an explicit symbol subset."""
    symbol_list = sorted(set(symbols))
    plan = build_query_plan(symbol_list, window_start=window_start, as_of=as_of)
    if dry_run:
        return {
            "STATUS": "DRY_RUN_OK",
            "REQUESTED_SYMBOL_N": len(symbol_list),
            "REQUESTED_SYMBOL_HASH": symbol_hash(symbol_list),
            "REQUIRED_ROW_N": 0,
            "FORMAL_FACT_ROW_N": 0,
            "MISSING_REQUIRED_N": 0,
            "PROVIDER_SUSPENDED_SUPERSET_N": 0,
            "UNEXPECTED_TRADED_N": 0,
            "TRADESTATUS_UNKNOWN_N": 0,
            "IDENTITY_FAILURE_N": 0,
            "DUPLICATE_N": 0,
            "POST_ASOF_N": 0,
            "INVALID_PRECLOSE_N": 0,
            "QUALITY_GATE_PASS": None,
            "NETWORK_PROVIDER_DATA_FETCH": "NO",
            "MARKET_DATA_WRITE": "NO",
            "formal_rows": [],
            "audit_summary": [],
            "QUERY_WINDOW_N": plan["QUERY_WINDOW_N"],
            "QUERY_PLAN_HASH": plan["QUERY_PLAN_HASH"],
            "query_plan": plan["query_plan"],
        }
    if provider_fetch is None:
        raise ValueError("provider_fetch is required for non-dry-run bounded adapter")
    required = load_required_keys(root, symbol_list, as_of=as_of, window_start=window_start)
    if required["duplicate_required_key_n"] != 0:
        raise RuntimeError("duplicate required key in authoritative daily_bars")
    if required["identity_scope_mismatch_n"] != 0:
        raise RuntimeError("identity scope mismatch in required key loader")
    if required["post_asof_n"] != 0:
        raise RuntimeError("post-ASOF required keys detected")
    provider_rows: list[dict[str, Any]] = []
    for window in plan["query_plan"]:
        fetched = provider_fetch(window)
        provider_rows.extend(fetched or [])
    normalized = normalize_baostock_preclose_rows(
        provider_rows, required["required_keys"], symbol_list, as_of=as_of
    )
    formal_rows = build_formal_facts(
        normalized["eligible_rows"],
        adapter_version=adapter_version,
        fetched_at=fetched_at,
    )
    counts = normalized["counts"]
    gate = quality_gate_pass(
        required_row_n=required["required_row_n"],
        formal_fact_row_n=len(formal_rows),
        missing_required_n=normalized["missing_required_n"],
        counts=counts,
        formal_rows=formal_rows,
    )
    return {
        "STATUS": "COMPLETE" if gate else "FAILED_QUALITY_GATE",
        "REQUESTED_SYMBOL_N": len(symbol_list),
        "REQUESTED_SYMBOL_HASH": symbol_hash(symbol_list),
        "REQUIRED_ROW_N": required["required_row_n"],
        "FORMAL_FACT_ROW_N": len(formal_rows),
        "MISSING_REQUIRED_N": normalized["missing_required_n"],
        "PROVIDER_SUSPENDED_SUPERSET_N": counts["PROVIDER_SUSPENDED_SUPERSET"],
        "UNEXPECTED_TRADED_N": counts["UNEXPECTED_TRADED"],
        "TRADESTATUS_UNKNOWN_N": counts["TRADESTATUS_UNKNOWN"],
        "IDENTITY_FAILURE_N": counts["IDENTITY_FAILURE"],
        "DUPLICATE_N": counts["DUPLICATE"],
        "POST_ASOF_N": counts["POST_ASOF"],
        "INVALID_PRECLOSE_N": counts["INVALID_PRECLOSE"],
        "QUALITY_GATE_PASS": gate,
        "NETWORK_PROVIDER_DATA_FETCH": "YES",
        "MARKET_DATA_WRITE": "NO",
        "formal_rows": formal_rows,
        "audit_summary": normalized["audit_rows"][:50],
        "QUERY_WINDOW_N": plan["QUERY_WINDOW_N"],
        "QUERY_PLAN_HASH": plan["QUERY_PLAN_HASH"],
    }


def compute_resumption_candidates(
    *,
    trading_dates: set[date],
    bars: pl.DataFrame,
    instrument_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Reuse the frozen, audited resumption semantic.

    If active-lifetime market trading days exist between two consecutive actual
    traded rows of a symbol and that symbol has no actual bar on those days,
    the later traded row is a RESUMPTION_CANDIDATE. No calendar-day heuristic.
    """
    candidates: list[dict[str, Any]] = []
    for group in bars.partition_by("symbol", as_dict=False):
        legend = str(group["symbol"][0])
        metadata = instrument_map.get(legend, {})
        listed = metadata.get("list_date")
        delisted = metadata.get("delist_date")
        rows = list(group.sort("trade_date").iter_rows(named=True))
        prior_date: date | None = None
        for row in rows:
            current = row["trade_date"]
            if prior_date is not None:
                active_missing = [
                    cal
                    for cal in trading_dates
                    if prior_date < cal < current
                    and (listed is None or cal >= listed)
                    and (delisted is None or cal <= delisted)
                ]
                if active_missing:
                    candidates.append(
                        {
                            "symbol": legend,
                            "trade_date": current.isoformat(),
                            "previous_trade_date": prior_date.isoformat(),
                            "gap_trading_day_n": len(active_missing),
                            "FIRST_TRADE_AFTER_SUSPENSION": True,
                        }
                    )
            prior_date = current
    candidate_keys = {
        (row["symbol"], parse_date(row["trade_date"])) for row in candidates
    }
    return {
        "resumption_candidates": candidates,
        "resumption_candidate_keys": candidate_keys,
    }


def verify_clean_normal_parity(
    *,
    formal_rows: list[dict[str, Any]],
    bars: pl.DataFrame,
    event_dates: set[tuple[str, date]],
    first_listing_dates: set[tuple[str, date]],
    resumption_keys: set[tuple[str, date]],
    known_special_keys: set[tuple[str, date]],
) -> dict[str, Any]:
    """Production clean-NORMAL full-parity verifier primitive.

    CLEAN_NORMAL excludes IPO first-listing days, corporate-action ex-dates,
    resumption candidates, and known special rows. PASS requires every
    comparable row to be display-exact; no +-0.01 mismatch is tolerated.
    """
    prev_map: dict[tuple[str, date], float | None] = {}
    for group in bars.partition_by("symbol", as_dict=False):
        legend = str(group["symbol"][0])
        prev: float | None = None
        for row in group.sort("trade_date").iter_rows(named=True):
            prev_map[(legend, row["trade_date"])] = prev
            prev = parse_float(row.get("close"))
    excluded = set(event_dates)
    excluded.update(first_listing_dates)
    excluded.update(resumption_keys)
    excluded.update(known_special_keys)
    clean: list[dict[str, Any]] = []
    max_diff = 0.0
    for row in formal_rows:
        key = (str(row["symbol"]), row["trade_date"])
        if key in excluded:
            continue
        local = prev_map.get(key)
        if local is None:
            continue
        observed = float(row["preclose"])
        exact = display_equal(observed, local)
        diff = abs(observed - local)
        max_diff = max(max_diff, diff)
        trade_date_str = (
            row["trade_date"].isoformat()
            if isinstance(row["trade_date"], date)
            else str(row["trade_date"])
        )
        clean.append(
            {
                "symbol": row["symbol"],
                "trade_date": trade_date_str,
                "previous_effective_close": round(float(local), 6),
                "baostock_preclose": round(observed, 6),
                "exact": exact,
                "diff": round(diff, 9),
            }
        )
    mismatch_n = sum(1 for item in clean if not item["exact"])
    return {
        "CLEAN_NORMAL_N": len(clean),
        "CLEAN_NORMAL_EXACT_N": len(clean) - mismatch_n,
        "CLEAN_NORMAL_MISMATCH_N": mismatch_n,
        "CLEAN_NORMAL_MAX_DIFF": round(max_diff, 9),
        "CLEAN_NORMAL_PARITY_STATUS": "PASS"
        if len(clean) > 0 and mismatch_n == 0
        else "FAIL",
    }


FROZEN_000564_CASE: dict[str, Any] = {
    "symbol": "000564.SZ",
    "trade_date": "2018-07-20",
    "classification": "RESUMPTION_CANDIDATE/SPECIAL (suspension-resume)",
    "previous_effective_close": 4.78,
    "suspended_period_ex_date": "2018-07-13",
    "official_ex_price": 4.77,
    "baostock_resume_preclose": 4.77,
}


def load_frozen_sentinel_evidence(repo_root: Path | None = None) -> list[dict[str, Any]]:
    """Load the frozen sentinel evidence set from local receipts + frozen case.

    Sentinels: 20 official event rows, 3 official IPO rows, and the single
    frozen 000564 2018-07-20 case. All come from local read-only receipts or
    the single FROZEN_000564_CASE constant above; no announcement re-fetch and
    no sentinel values scattered across production logic.
    """
    root = (repo_root or REPO_ROOT).resolve()
    closure_path = root / CLOSURE_RECEIPT_REL
    if not closure_path.exists():
        raise FileNotFoundError(f"frozen closure receipt missing: {closure_path}")
    try:
        closure = json.loads(closure_path.read_text(encoding="utf-8"))
        report = closure.get("report", {})
        events = report.get("OFFICIAL_EVENT_DETAILS", [])
        ipos = report.get("IPO_DETAILS", [])
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot read frozen closure receipt: {closure_path}") from exc
    sentinels: list[dict[str, Any]] = []
    for row in events:
        sentinels.append(
            {
                "symbol": row["symbol"],
                "date": str(row["ex_date"]),
                "official_reference": float(row["official_display_preclose"]),
                "baostock_preclose": float(row["baostock_preclose"]),
                "kind": "OFFICIAL_EVENT",
            }
        )
    for row in ipos:
        sentinels.append(
            {
                "symbol": row["symbol"],
                "date": str(row["listing_date"]),
                "official_reference": float(row["official_issue_price"]),
                "baostock_preclose": float(row["baostock_preclose"]),
                "kind": "IPO",
            }
        )
    sentinels.append(
        {
            "symbol": FROZEN_000564_CASE["symbol"],
            "date": FROZEN_000564_CASE["trade_date"],
            "official_reference": float(FROZEN_000564_CASE["official_ex_price"]),
            "baostock_preclose": float(FROZEN_000564_CASE["baostock_resume_preclose"]),
            "kind": "FROZEN_SPECIAL_CASE",
        }
    )
    return sentinels


def verify_frozen_sentinels(sentinels: list[dict[str, Any]]) -> dict[str, Any]:
    """Sentinel gate: every frozen official sentinel must be exact vs BaoStock."""
    if not sentinels:
        return {
            "SENTINEL_N": 0,
            "SENTINEL_EXACT_N": 0,
            "SENTINEL_MISMATCH_N": 0,
            "FROZEN_OFFICIAL_SENTINEL_PASS": False,
        }
    mismatch_n = sum(
        1
        for row in sentinels
        if not display_equal(
            float(row["official_reference"]), float(row["baostock_preclose"])
        )
    )
    total = len(sentinels)
    return {
        "SENTINEL_N": total,
        "SENTINEL_EXACT_N": total - mismatch_n,
        "SENTINEL_MISMATCH_N": mismatch_n,
        "FROZEN_OFFICIAL_SENTINEL_PASS": mismatch_n == 0,
    }


def real_root_identity(root: Path) -> dict[str, Any]:
    """Read-only R3 identity recheck from the real root receipt + scan."""
    receipt_path = root / "meta" / "asl" / "r3" / "r3-identity-receipt.json"
    receipt_ok = False
    receipt_n = receipt_hash = None
    if receipt_path.exists():
        try:
            original = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt_n = original.get("formal_identity_n")
            receipt_hash = original.get("formal_identity_hash")
            receipt_ok = bool(
                receipt_n == FORMAL_IDENTITY_N
                and receipt_hash == FORMAL_IDENTITY_HASH
                and original.get("shsz_identity_complete") is True
            )
        except (OSError, ValueError):
            receipt_ok = False
    daily_glob = sorted((root / "curated" / "daily_bars").rglob("*.parquet"))
    if daily_glob:
        actual_symbols = sorted(
            {
                str(s)
                for s in (
                    pl.scan_parquet(str(root / "curated" / "daily_bars" / "**" / "*.parquet"))
                    .select("symbol")
                    .collect()
                    .get_column("symbol")
                    .to_list()
                )
            }
        )
    else:
        actual_symbols = []
    actual_n = len(actual_symbols)
    actual_hash = symbol_hash(actual_symbols)
    identity_match = bool(
        receipt_ok
        and actual_n == FORMAL_IDENTITY_N
        and actual_hash == FORMAL_IDENTITY_HASH
    )
    return {
        "R4A0_READY": identity_match,
        "formal_identity_n": actual_n,
        "formal_identity_hash": actual_hash,
        "receipt_match": receipt_ok,
        "IDENTITY_SOURCE": "real root daily_bars unique symbols + r3-identity-receipt",
    }
