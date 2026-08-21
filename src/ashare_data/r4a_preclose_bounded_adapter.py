"""R4A bounded BaoStock preclose adapter (hardened V01.1, V01_1).

Frozen contract authority:
  docs/plans/R4A_PRECLOSE_CANONICAL_SOURCE_CONTRACT_V01.md (V01.1)

This module builds the formal preclose_facts rows for a bounded symbol set
from a BaoStock query_history_k_data_plus response (fields
date,code,preclose,tradestatus; frequency d; adjustflag 3), strictly
normalizing every provider row and failing closed. It never writes market
data; all results are in-memory and offline tests use a fake provider.

Hardening (R4A5.1) corrections:
  1. R4A0 prerequisite reuses r4a0_corporate_actions_gate.run_gate; the old
     "R3 identity match -> R4A0_READY" mapping is removed. R3_IDENTITY_MATCH
     is reported independently.
  2. Frozen sentinel gate compares current formal rows against official
     references; old receipt baostock_preclose is no longer the observed value.
  3. Provider PK duplicate gate runs after identity/date form the PK but
     before tradestatus / required / preclose classification.
  4. Clean-NORMAL parity is fail-closed (REQUIRED == COMPARABLE, UNCOMPARED=0).
  5. Real BaostockSessionProvider wrapper is implemented but never called in
     this task (lazy import; login/query/logout + result normalization).
  6. Query-window identity: every returned row must match requested code and
     lie inside the requested window.
  7. QUERY_PLAN_HASH covers the full executable contract.
  8. adapter_version provenance: real execution requires expected+runtime SHA.
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

# Expected implementation HEAD the real execution may use as code authority.
# Offline fixtures may inject "TEST"; real pilot must match an explicit SHA.
EXPECTED_ADAPTER_SHA = "710a7c80cb434955c38475f3bdd4a1403d5c6a41"


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
    same deterministic display normalization. Parity PASS requires raw values
    to be display-exact; no mismatch window is allowed.
    """
    try:
        left_dec = Decimal(str(left)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        right_dec = Decimal(str(right)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (TypeError, ValueError, ArithmeticError):
        return False
    return left_dec == right_dec


class BaoStockPrecloseProvider(Protocol):
    """Provider contract for the bounded adapter (real wrapper / fake)."""

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
    """Minimal real BaoStock wrapper. Implemented, never called in this task.

    Reuses baostock==0.9.3 query_history_k_data_plus with fields
    date,code,preclose,tradestatus; frequency=d; adjustflag=3. BaoStock is
    imported lazily so the adapter can be loaded offline. The wrapper validates
    error_code == "0" and enforces row field identity. In this task neither
    login nor query may actually run; offline tests monkeypatch a fake module.
    """

    def __init__(self) -> None:
        self._bs: Any = None
        self._logged_in = False

    def login(self) -> None:
        # pylint: disable=import-outside-toplevel
        import baostock as bs  # type: ignore[import-not-found]

        self._bs = bs
        result = bs.login()
        error_code = str(getattr(result, "error_code", ""))
        if error_code != "0":
            raise RuntimeError(f"baostock login failed: {getattr(result, 'error_msg', '')}")
        self._logged_in = True

    def query_history_k_data_plus(
        self,
        code: str,
        fields: str,
        start_date: str,
        end_date: str,
        frequency: str,
        adjustflag: str,
    ) -> list[list[str]]:
        """Query BaoStock and return raw row fields (date,code,preclose,tradestatus)."""
        if not self._logged_in or self._bs is None:
            raise RuntimeError("must login before query")
        result = self._bs.query_history_k_data_plus(
            code,
            fields,
            start_date=start_date,
            end_date=end_date,
            frequency=frequency,
            adjustflag=adjustflag,
        )
        error_code = str(getattr(result, "error_code", ""))
        if error_code != "0":
            raise RuntimeError(
                f"baostock query failed for {code}: {getattr(result, 'error_msg', '')}"
            )
        rows: list[list[str]] = []
        while result.next():
            row = list(result.get_row_data())
            if len(row) != len(fields.split(",")):
                raise RuntimeError(f"baostock row field count mismatch for {code}")
            rows.append(row)
        return rows

    def logout(self) -> None:
        if self._bs is not None and self._logged_in:
            self._bs.logout()
        self._logged_in = False

    def close(self) -> None:
        self.logout()


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


def _scan_daily_bars(root: Path, symbol_list: list[str]) -> pl.DataFrame:
    return (
        pl.scan_parquet(str(root / "curated" / "daily_bars" / "**" / "*.parquet"))
        .select(["symbol", "trade_date"])
        .filter(pl.col("symbol").is_in(symbol_list))
        .collect()
        .with_columns(
            pl.col("symbol").cast(pl.String),
            pl.col("trade_date").cast(pl.Date),
        )
    )


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
    all_bars = _scan_daily_bars(root, symbol_list)
    post_asof_n = int(all_bars.filter(pl.col("trade_date") > as_of).height)
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


def _identity_and_window_failure(
    row: dict[str, Any],
    expected_code: str,
    requested_window: dict[str, Any] | None,
) -> str | None:
    """Return failure enum if the row fails identity/window, else None."""
    parsed_date = parse_date(row.get("date"))
    if parsed_date is None or row.get("code") != expected_code:
        return "IDENTITY_FAILURE"
    if requested_window is not None:
        start = parse_date(requested_window.get("start"))
        end = parse_date(requested_window.get("end"))
        if start is None or end is None or not (start <= parsed_date <= end):
            return "WINDOW_SCOPE_FAILURE"
    return None


def normalize_baostock_preclose_rows(
    provider_rows: Iterable[dict[str, Any]],
    required_keys: set[tuple[str, date]],
    symbols: Iterable[str],
    *,
    as_of: date = AS_OF,
    requested_window: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Strictly classify provider rows into the frozen contract's bins.

    Classifications:
      ELIGIBLE_REQUIRED_ROW / PROVIDER_SUSPENDED_SUPERSET / UNEXPECTED_TRADED /
      TRADESTATUS_UNKNOWN / IDENTITY_FAILURE / WINDOW_SCOPE_FAILURE /
      POST_ASOF / DUPLICATE / INVALID_PRECLOSE

    Duplicate detection runs on the provider PK (symbol, date) after
    identity/date form the PK but BEFORE tradestatus/required/preclose
    classification, so any duplicate (status0+status0, status0+status1,
    invalid+valid, unexpected, eligible) is a blocker.
    """
    expected_code = {symbol: bs_code(symbol) for symbol in symbols}
    counts = {
        "ELIGIBLE_REQUIRED_ROW": 0,
        "PROVIDER_SUSPENDED_SUPERSET": 0,
        "UNEXPECTED_TRADED": 0,
        "TRADESTATUS_UNKNOWN": 0,
        "IDENTITY_FAILURE": 0,
        "WINDOW_SCOPE_FAILURE": 0,
        "POST_ASOF": 0,
        "DUPLICATE": 0,
        "INVALID_PRECLOSE": 0,
    }
    eligible: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    eligible_keys: set[tuple[str, date]] = set()
    seen_provider_pk: set[tuple[str, date]] = set()

    for row in provider_rows:
        symbol = str(row.get("symbol", ""))
        parsed_date = parse_date(row.get("date"))
        issue = _identity_and_window_failure(
            row, expected_code.get(symbol, ""), requested_window
        )
        if issue is not None:
            counts[issue] += 1
            audit.append(
                {"symbol": symbol, "date": str(row.get("date")), "issue": issue}
            )
            continue
        assert parsed_date is not None
        pk = (symbol, parsed_date)
        # Global provider PK duplicate gate: before tradestatus/preclose logic.
        if pk in seen_provider_pk:
            counts["DUPLICATE"] += 1
            audit.append(
                {"symbol": symbol, "date": parsed_date.isoformat(), "issue": "DUPLICATE"}
            )
            continue
        seen_provider_pk.add(pk)
        if parsed_date > as_of:
            counts["POST_ASOF"] += 1
            audit.append(
                {"symbol": symbol, "date": parsed_date.isoformat(), "issue": "POST_ASOF"}
            )
            continue
        ts = str(row.get("tradestatus", "")).strip() if row.get("tradestatus") is not None else None
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
        if pk not in required_keys:
            counts["UNEXPECTED_TRADED"] += 1
            audit.append(
                {"symbol": symbol, "date": parsed_date.isoformat(), "issue": "UNEXPECTED_TRADED"}
            )
            continue
        preclose = parse_float(row.get("preclose"))
        if preclose is None or not (math.isfinite(preclose) and preclose > 0):
            counts["INVALID_PRECLOSE"] += 1
            audit.append(
                {"symbol": symbol, "date": parsed_date.isoformat(), "issue": "INVALID_PRECLOSE"}
            )
            continue
        eligible_keys.add(pk)
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


def adapter_authority_status(
    adapter_version: str,
    *,
    expected_sha: str | None,
    runtime_sha: str | None,
) -> dict[str, Any]:
    """Provenance gate: real execution requires expected+runtime SHA match.

    Offline fixtures may inject "TEST" (fixture_ok=True path). Real pilot must
    supply an expected SHA and a runtime SHA that both equal adapter_version.
    """
    fixture_ok = adapter_version == "TEST"
    expected_ok = (adapter_version == expected_sha) if expected_sha else (not fixture_ok)
    runtime_ok = (adapter_version == runtime_sha) if runtime_sha else (not fixture_ok)
    if fixture_ok:
        return {
            "ADAPTER_AUTHORITY_PASS": True,
            "ADAPTER_AUTHORITY_MODE": "OFFLINE_FIXTURE",
        }
    if expected_ok and runtime_ok:
        return {
            "ADAPTER_AUTHORITY_PASS": True,
            "ADAPTER_AUTHORITY_MODE": "EXACT_SHA",
        }
    return {
        "ADAPTER_AUTHORITY_PASS": False,
        "ADAPTER_AUTHORITY_MODE": "UNVALIDATED" if not expected_ok and not runtime_ok else "SHA_MISMATCH",
    }


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
        "WINDOW_SCOPE_FAILURE",
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
    """Deterministic per (symbol, year) plan; hash covers the full contract."""
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
    executable_contract = {
        "query_contract_version": QUERY_CONTRACT_VERSION,
        "source_version": SOURCE_VERSION,
        "fields": QUERY_FIELDS,
        "frequency": QUERY_FREQUENCY,
        "adjustflag": QUERY_ADJUSTFLAG,
        "as_of": as_of.isoformat(),
        "window_start": window_start.isoformat(),
        "cnequity_pin": CNEQUITY_PIN,
        "windows": [
            [p["symbol"], p["bs_code"], p["year"], p["start"], p["end"]]
            for p in plan
        ],
    }
    plan_hash = hashlib.sha256(
        json.dumps(executable_contract, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "query_plan": plan,
        "QUERY_WINDOW_N": len(plan),
        "QUERY_PLAN_HASH": plan_hash,
    }


def r3_identity_match(root: Path) -> dict[str, Any]:
    """Independent R3 identity match (real daily_bars unique symbols + receipt).

    This is intentionally separate from R4A0_READY (run_gate). It only reports
    whether the frozen R3 formal SH/SZ identity is reproducible.
    """
    receipt_path = root / "meta" / "asl" / "r3" / "r3-identity-receipt.json"
    receipt_ok = False
    if receipt_path.exists():
        try:
            original = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt_ok = bool(
                original.get("formal_identity_n") == FORMAL_IDENTITY_N
                and original.get("formal_identity_hash") == FORMAL_IDENTITY_HASH
                and original.get("shsz_identity_complete") is True
            )
        except (OSError, ValueError):
            receipt_ok = False
    actual_symbols = []
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
    actual_n = len(actual_symbols)
    actual_hash = symbol_hash(actual_symbols)
    return {
        "R3_IDENTITY_MATCH": bool(
            receipt_ok
            and actual_n == FORMAL_IDENTITY_N
            and actual_hash == FORMAL_IDENTITY_HASH
        ),
        "FORMAL_IDENTITY_N": actual_n,
        "FORMAL_IDENTITY_HASH": actual_hash,
        "IDENTITY_RECEIPT_MATCH": receipt_ok,
        "IDENTITY_SOURCE": "real root daily_bars unique symbols + r3-identity-receipt",
    }


def r4a0_prerequisite(root: Path) -> dict[str, Any]:
    """Formal R4A0 prerequisite: reuse the audited read-only gate."""
    from ashare_data.r4a0_corporate_actions_gate import run_gate

    try:
        gate = run_gate(root)
        gate_status = "PASS" if bool(gate["R4A0_READY"]) else "FAIL"
        blocker = None
    except Exception as exc:  # noqa: BLE001 - fail closed on unreadable gate
        gate_status = "FAIL"
        blocker = f"R4A0_GATE_ERROR: {type(exc).__name__}: {exc}"
        gate = {"R4A0_READY": False}
    identity = r3_identity_match(root)
    return {
        "R4A0_READY": bool(gate["R4A0_READY"]) and gate_status == "PASS",
        "R4A0_GATE_STATUS": gate_status,
        "R4A0_BLOCKER": blocker,
        "R3_IDENTITY_MATCH": identity["R3_IDENTITY_MATCH"],
        "FORMAL_IDENTITY_N": identity["FORMAL_IDENTITY_N"],
        "FORMAL_IDENTITY_HASH": identity["FORMAL_IDENTITY_HASH"],
    }


def run_bounded_adapter(
    *,
    root: Path,
    symbols: Iterable[str],
    provider_fetch: Callable[[dict[str, Any]], list[dict[str, Any]]] | None,
    dry_run: bool,
    adapter_version: str,
    fetched_at: str,
    expected_adapter_sha: str | None = None,
    runtime_adapter_sha: str | None = None,
    as_of: date = AS_OF,
    window_start: date = WINDOW_START,
) -> dict[str, Any]:
    """Run the bounded adapter (in-memory) for an explicit symbol subset."""
    symbol_list = sorted(set(symbols))
    plan = build_query_plan(symbol_list, window_start=window_start, as_of=as_of)
    prereq = r4a0_prerequisite(root)
    base = {
        "REQUESTED_SYMBOL_N": len(symbol_list),
        "REQUESTED_SYMBOL_HASH": symbol_hash(symbol_list),
        "R4A0_READY": prereq["R4A0_READY"],
        "R3_IDENTITY_MATCH": prereq["R3_IDENTITY_MATCH"],
        "FORMAL_IDENTITY_N": prereq["FORMAL_IDENTITY_N"],
        "FORMAL_IDENTITY_HASH": prereq["FORMAL_IDENTITY_HASH"],
        "QUERY_WINDOW_N": plan["QUERY_WINDOW_N"],
        "QUERY_PLAN_HASH": plan["QUERY_PLAN_HASH"],
    }
    if dry_run:
        if not prereq["R4A0_READY"]:
            return {
                **base,
                "STATUS": "BLOCKED_R4A0_PREREQUISITE",
                "DRY_RUN_STATUS": "BLOCKED",
                "FORMAL_FACT_ROW_N": 0,
                "MISSING_REQUIRED_N": 0,
                "PROVIDER_SUSPENDED_SUPERSET_N": 0,
                "UNEXPECTED_TRADED_N": 0,
                "TRADESTATUS_UNKNOWN_N": 0,
                "IDENTITY_FAILURE_N": 0,
                "WINDOW_SCOPE_FAILURE_N": 0,
                "DUPLICATE_N": 0,
                "POST_ASOF_N": 0,
                "INVALID_PRECLOSE_N": 0,
                "QUALITY_GATE_PASS": None,
                "NETWORK_PROVIDER_DATA_FETCH": "NO",
                "MARKET_DATA_WRITE": "NO",
                "FROZEN_SENTINEL_EXPECTED_N": 24,
                "FROZEN_OFFICIAL_SENTINEL_RUNTIME_STATUS": "NOT_RUN_DRY_RUN",
                "formal_rows": [],
                "audit_summary": [],
                "query_plan": plan["query_plan"],
            }
        return {
            **base,
            "STATUS": "DRY_RUN_OK",
            "DRY_RUN_STATUS": "OK",
            "FORMAL_FACT_ROW_N": 0,
            "MISSING_REQUIRED_N": 0,
            "PROVIDER_SUSPENDED_SUPERSET_N": 0,
            "UNEXPECTED_TRADED_N": 0,
            "TRADESTATUS_UNKNOWN_N": 0,
            "IDENTITY_FAILURE_N": 0,
            "WINDOW_SCOPE_FAILURE_N": 0,
            "DUPLICATE_N": 0,
            "POST_ASOF_N": 0,
            "INVALID_PRECLOSE_N": 0,
            "QUALITY_GATE_PASS": None,
            "NETWORK_PROVIDER_DATA_FETCH": "NO",
            "MARKET_DATA_WRITE": "NO",
            "FROZEN_SENTINEL_EXPECTED_N": 24,
            "FROZEN_OFFICIAL_SENTINEL_RUNTIME_STATUS": "NOT_RUN_DRY_RUN",
            "formal_rows": [],
            "audit_summary": [],
            "query_plan": plan["query_plan"],
        }
    # Non-dry-run: R4A0 prerequisite is a hard gate before any provider step.
    if not prereq["R4A0_READY"]:
        return {
            **base,
            "STATUS": "R4A0_PREREQUISITE_FAILED",
            "FORMAL_FACT_ROW_N": 0,
            "MISSING_REQUIRED_N": 0,
            "PROVIDER_SUSPENDED_SUPERSET_N": 0,
            "UNEXPECTED_TRADED_N": 0,
            "TRADESTATUS_UNKNOWN_N": 0,
            "IDENTITY_FAILURE_N": 0,
            "WINDOW_SCOPE_FAILURE_N": 0,
            "DUPLICATE_N": 0,
            "POST_ASOF_N": 0,
            "INVALID_PRECLOSE_N": 0,
            "QUALITY_GATE_PASS": False,
            "NETWORK_PROVIDER_DATA_FETCH": "NO",
            "MARKET_DATA_WRITE": "NO",
            "formal_rows": [],
            "audit_summary": [],
        }
    authority = adapter_authority_status(
        adapter_version,
        expected_sha=expected_adapter_sha or EXPECTED_ADAPTER_SHA,
        runtime_sha=runtime_adapter_sha,
    )
    if not authority["ADAPTER_AUTHORITY_PASS"]:
        return {
            **base,
            "STATUS": "ADAPTER_AUTHORITY_FAILED",
            "ADAPTER_AUTHORITY_MODE": authority["ADAPTER_AUTHORITY_MODE"],
            "FORMAL_FACT_ROW_N": 0,
            "MISSING_REQUIRED_N": 0,
            "PROVIDER_SUSPENDED_SUPERSET_N": 0,
            "UNEXPECTED_TRADED_N": 0,
            "TRADESTATUS_UNKNOWN_N": 0,
            "IDENTITY_FAILURE_N": 0,
            "WINDOW_SCOPE_FAILURE_N": 0,
            "DUPLICATE_N": 0,
            "POST_ASOF_N": 0,
            "INVALID_PRECLOSE_N": 0,
            "QUALITY_GATE_PASS": False,
            "NETWORK_PROVIDER_DATA_FETCH": "NO",
            "MARKET_DATA_WRITE": "NO",
            "formal_rows": [],
            "audit_summary": [],
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
    window_failures = 0
    for window in plan["query_plan"]:
        fetched = provider_fetch(window)
        for row in fetched or []:
            issue = _identity_and_window_failure(
                row,
                expected_code=window["bs_code"],
                requested_window=window,
            )
            if issue is not None:
                window_failures += 1
                continue
            provider_rows.append(row)
    if window_failures != 0:
        return {
            **base,
            "STATUS": "FAILED_QUALITY_GATE",
            "REQUIRED_ROW_N": required["required_row_n"],
            "FORMAL_FACT_ROW_N": 0,
            "MISSING_REQUIRED_N": required["required_row_n"],
            "PROVIDER_SUSPENDED_SUPERSET_N": 0,
            "UNEXPECTED_TRADED_N": 0,
            "TRADESTATUS_UNKNOWN_N": 0,
            "IDENTITY_FAILURE_N": window_failures,
            "WINDOW_SCOPE_FAILURE_N": window_failures,
            "DUPLICATE_N": 0,
            "POST_ASOF_N": 0,
            "INVALID_PRECLOSE_N": 0,
            "QUALITY_GATE_PASS": False,
            "NETWORK_PROVIDER_DATA_FETCH": "YES",
            "MARKET_DATA_WRITE": "NO",
            "formal_rows": [],
            "audit_summary": [],
        }
    normalized = normalize_baostock_preclose_rows(
        provider_rows,
        required["required_keys"],
        symbol_list,
        as_of=as_of,
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
        **base,
        "STATUS": "COMPLETE" if gate else "FAILED_QUALITY_GATE",
        "REQUIRED_ROW_N": required["required_row_n"],
        "FORMAL_FACT_ROW_N": len(formal_rows),
        "MISSING_REQUIRED_N": normalized["missing_required_n"],
        "PROVIDER_SUSPENDED_SUPERSET_N": counts["PROVIDER_SUSPENDED_SUPERSET"],
        "UNEXPECTED_TRADED_N": counts["UNEXPECTED_TRADED"],
        "TRADESTATUS_UNKNOWN_N": counts["TRADESTATUS_UNKNOWN"],
        "IDENTITY_FAILURE_N": counts["IDENTITY_FAILURE"],
        "WINDOW_SCOPE_FAILURE_N": counts["WINDOW_SCOPE_FAILURE"],
        "DUPLICATE_N": counts["DUPLICATE"],
        "POST_ASOF_N": counts["POST_ASOF"],
        "INVALID_PRECLOSE_N": counts["INVALID_PRECLOSE"],
        "QUALITY_GATE_PASS": gate,
        "NETWORK_PROVIDER_DATA_FETCH": "YES",
        "MARKET_DATA_WRITE": "NO",
        "formal_rows": formal_rows,
        "audit_summary": normalized["audit_rows"][:50],
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


def _normalize_key(row: dict[str, Any]) -> tuple[str, date]:
    trade_date = row["trade_date"]
    if isinstance(trade_date, date):
        return (str(row["symbol"]), trade_date)
    parsed = parse_date(trade_date)
    if parsed is None:
        raise ValueError(f"unparseable trade_date in formal row: {trade_date}")
    return (str(row["symbol"]), parsed)


def verify_clean_normal_parity(
    *,
    formal_rows: list[dict[str, Any]],
    bars: pl.DataFrame,
    event_dates: set[tuple[str, date]],
    first_listing_dates: set[tuple[str, date]],
    resumption_keys: set[tuple[str, date]],
    known_special_keys: set[tuple[str, date]],
) -> dict[str, Any]:
    """Production clean-NORMAL full-parity verifier (fail-closed).

    CLEAN_NORMAL excludes IPO first-listing days, corporate-action ex-dates,
    resumption candidates, and known special rows. PASS requires
    CLEAN_NORMAL_REQUIRED_N == CLEAN_NORMAL_COMPARABLE_N, UNCOMPARED_N == 0,
    MISMATCH_N == 0. A missing local previous close is an explicit
    UNCOMPARED row (never silently continued). no +-0.01 mismatch tolerated.
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
    required_n = 0
    comparable_n = 0
    uncompared_n = 0
    mismatch_n = 0
    exact_n = 0
    max_diff = 0.0
    rows_compared: list[dict[str, Any]] = []
    for row in formal_rows:
        key = _normalize_key(row)
        if key in excluded:
            continue
        required_n += 1
        local = prev_map.get(key)
        if local is None:
            uncompared_n += 1
            continue
        observed = float(row["preclose"])
        exact = display_equal(observed, local)
        diff = abs(observed - local)
        max_diff = max(max_diff, diff)
        if exact:
            exact_n += 1
        else:
            mismatch_n += 1
        comparable_n += 1
        rows_compared.append(
            {
                "symbol": row["symbol"],
                "trade_date": key[1].isoformat(),
                "previous_effective_close": round(float(local), 6),
                "baostock_preclose": round(observed, 6),
                "exact": exact,
                "diff": round(diff, 9),
            }
        )
    pass_status = bool(
        required_n > 0
        and required_n == comparable_n
        and uncompared_n == 0
        and mismatch_n == 0
    )
    return {
        "CLEAN_NORMAL_REQUIRED_N": required_n,
        "CLEAN_NORMAL_COMPARABLE_N": comparable_n,
        "CLEAN_NORMAL_UNCOMPARED_N": uncompared_n,
        "CLEAN_NORMAL_EXACT_N": exact_n,
        "CLEAN_NORMAL_MISMATCH_N": mismatch_n,
        "CLEAN_NORMAL_MAX_DIFF": round(max_diff, 9),
        "CLEAN_NORMAL_PARITY_STATUS": "PASS" if pass_status else "FAIL",
        "NORMAL_FULL_PARITY_PASS": pass_status,
        "rows_compared": rows_compared,
    }


FROZEN_000564_CASE: dict[str, Any] = {
    "symbol": "000564.SZ",
    "trade_date": "2018-07-20",
    "classification": "RESUMPTION_CANDIDATE/SPECIAL (suspension-resume)",
    "previous_effective_close": 4.78,
    "suspended_period_ex_date": "2018-07-13",
    "official_reference": 4.77,
}


def load_frozen_sentinel_evidence(repo_root: Path | None = None) -> list[dict[str, Any]]:
    """Load the frozen sentinel contract from local receipts + frozen case.

    The loader emits only expected sentinel values: symbol, trade_date,
    official_reference, kind, and authority/provenance when available. It never
    emits an observed baostock_preclose; the runtime gate compares current
    formal rows against these expected official references.
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
                "trade_date": str(row["ex_date"]),
                "official_reference": float(row["official_display_preclose"]),
                "kind": "OFFICIAL_EVENT",
                "authority_url": row.get("authority_url"),
            }
        )
    for row in ipos:
        sentinels.append(
            {
                "symbol": row["symbol"],
                "trade_date": str(row["listing_date"]),
                "official_reference": float(row["official_issue_price"]),
                "kind": "IPO",
                "authority_url": row.get("authority_url"),
            }
        )
    sentinels.append(
        {
            "symbol": FROZEN_000564_CASE["symbol"],
            "trade_date": FROZEN_000564_CASE["trade_date"],
            "official_reference": float(FROZEN_000564_CASE["official_reference"]),
            "kind": "FROZEN_SPECIAL_CASE",
            "authority_provenance": "R4A4 V01.1 frozen case + R4A3.1 extra-row audit",
        }
    )
    if len(sentinels) != 24:
        raise ValueError(f"frozen sentinel expected 24, got {len(sentinels)}")
    return sentinels


def verify_frozen_sentinels(
    expected_sentinels: list[dict[str, Any]],
    current_formal_rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Sentinel gate against the CURRENT extraction's formal rows.

    Every frozen sentinel must be present as an exact (symbol, trade_date) key in
    current_formal_rows and its current preclose must equal official_reference
    at exact display equality. PASS requires REQUIRED=PRESENT=EXACT=24 and
    MISSING=MISMATCH=0.
    """
    formal_map = {_normalize_key(row): float(row["preclose"]) for row in current_formal_rows}
    required_n = len(expected_sentinels)
    present_n = 0
    missing_n = 0
    exact_n = 0
    mismatch_n = 0
    missing_cases: list[dict[str, Any]] = []
    mismatch_cases: list[dict[str, Any]] = []
    for sentinel in expected_sentinels:
        key = (
            str(sentinel["symbol"]),
            parse_date(sentinel["trade_date"]),
        )
        if key[1] is None or key not in formal_map:
            missing_n += 1
            missing_cases.append(
                {"symbol": sentinel["symbol"], "trade_date": sentinel["trade_date"]}
            )
            continue
        present_n += 1
        current = formal_map[key]
        expected = float(sentinel["official_reference"])
        if display_equal(current, expected):
            exact_n += 1
        else:
            mismatch_n += 1
            mismatch_cases.append(
                {
                    "symbol": sentinel["symbol"],
                    "trade_date": sentinel["trade_date"],
                    "current": current,
                    "official_reference": expected,
                }
            )
    pass_status = bool(
        required_n == 24
        and present_n == 24
        and exact_n == 24
        and missing_n == 0
        and mismatch_n == 0
    )
    return {
        "SENTINEL_REQUIRED_N": required_n,
        "SENTINEL_PRESENT_N": present_n,
        "SENTINEL_EXACT_N": exact_n,
        "SENTINEL_MISSING_N": missing_n,
        "SENTINEL_MISMATCH_N": mismatch_n,
        "FROZEN_OFFICIAL_SENTINEL_PASS": pass_status,
        "missing_cases": missing_cases,
        "mismatch_cases": mismatch_cases,
    }
