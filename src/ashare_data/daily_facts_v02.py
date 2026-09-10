"""Daily Facts V02 candidate generator (CNEquity 0.8.0 contract).

This module derives the five Daily Facts fields from CNEquity datasets and
local authority only. It is deliberately unable to read the frozen BaoStock
Daily Facts V1 evidence: the shadow comparison is a separate step, so a
candidate can never be built from the answer it will be compared against.

Contract: docs/contracts/DAILY_FACTS_V02_SOURCE_CONTRACT_V01.md

Two decisions that shape the code:

* Decision A - a CNEquity ``delisted`` status is a lifecycle fact. It never
  becomes a daily value; inside a frozen eligible universe it is a
  certification blocker (``ELIGIBILITY_LIFECYCLE_CONFLICT``).
* Decision B - the turnover denominator is a strict point-in-time
  ``float_shares``: both ``change_date`` and ``announce_date`` must be on or
  before the trade date. No future value may stand in.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ashare_data.reference_price_evidence import (
    CashDividendReferenceEvidence,
    ReferencePriceEvidenceError,
    load_reference_price_evidence,
)

SCHEMA = "ASL_DAILY_FACTS_V02"

#: Exchange display tick. Every published price is a multiple of it, so a
#: derived reference price must be quantized before it can be published.
DISPLAY_TICK = Decimal("0.01")

#: Derivation rule ids. A published fact names the rule that produced it so a
#: later contract change cannot silently reinterpret existing rows.
RULE_PRECLOSE_PRIOR_CLOSE = "PRIOR_PUBLISHED_CLOSE_V02"
RULE_PRECLOSE_REFERENCE_PRICE = "EXCHANGE_REFERENCE_PRICE_V02"
RULE_PCT_CHG = "CLOSE_OVER_PRECLOSE_V02"
RULE_TURNOVER = "VOLUME_OVER_PIT_FLOAT_SHARES_V02"
RULE_STATUS_DATASET = "CNEQUITY_TRADING_STATUS_V02"

SOURCE_PRIOR_R3_CLOSE = "R3_PUBLISHED_CLOSE"
SOURCE_REFERENCE_PRICE = "CORPORATE_ACTION_OFFICIAL_REFERENCE_PRICE"

TRADING = "TRADING"
SUSPENDED = "SUSPENDED"
UNKNOWN = "UNKNOWN"

#: CNEquity 0.8.0 ``trading_status.status`` -> ASL trade_status. ``delisted``
#: is absent on purpose: Decision A resolves it through the lifecycle rule
#: instead of inventing a daily value.
STATUS_TO_TRADE_STATUS: dict[str, str] = {
    "normal": TRADING,
    "suspended": SUSPENDED,
    "delisted": "__LIFECYCLE__",
}

#: Filesystem tokens that identify the frozen V1 evidence. ``load_sources``
#: refuses any path containing one, so the generator cannot read the answer.
FORBIDDEN_PATH_TOKENS: tuple[str, ...] = (
    "raw/baostock",
    "raw/official_disclosures",
    "daily_facts_phase1",
    "part-full-eligible",
    "part-vertical-slice",
    "published-daily-facts-authority",
)


class DailyFactsV02Error(RuntimeError):
    """Stable error code for a V02 derivation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - Decimal raises several types
        raise DailyFactsV02Error("V02_INVALID_NUMERIC", f"{field} is not numeric") from exc
    if not parsed.is_finite():
        raise DailyFactsV02Error("V02_INVALID_NUMERIC", f"{field} is not finite")
    return parsed


def display_reference_price(
    prior_close: Any,
    *,
    cash_dividend: Any = 0,
    bonus_ratio: Any = 0,
    transfer_ratio: Any = 0,
    allotment_ratio: Any = 0,
    allotment_price: Any = 0,
) -> Decimal:
    """Exchange ex-rights reference price, quantized to the display tick.

    ``(P_prev - cash + allot_price * allot_ratio) / (1 + bonus + transfer +
    allot_ratio)``, rounded half-up to 0.01. The rounding is not cosmetic:
    603993.SH moves 18.845 to 18.85, and truncation would publish 18.84.
    """
    prior = _decimal(prior_close, "prior_close")
    cash = _decimal(cash_dividend or 0, "cash_dividend")
    bonus = _decimal(bonus_ratio or 0, "bonus_ratio")
    transfer = _decimal(transfer_ratio or 0, "transfer_ratio")
    allot_ratio = _decimal(allotment_ratio or 0, "allotment_ratio")
    allot_price = _decimal(allotment_price or 0, "allotment_price")
    denominator = Decimal(1) + bonus + transfer + allot_ratio
    if denominator <= 0:
        raise DailyFactsV02Error("V02_INVALID_DENOMINATOR", "share-change denominator is not positive")
    raw = (prior - cash + allot_price * allot_ratio) / denominator
    return raw.quantize(DISPLAY_TICK, rounding=ROUND_HALF_UP)


def derive_pct_chg(close: Any, preclose: Any) -> float:
    """Percentage change against the canonical preclose, in percent points."""
    close_d = _decimal(close, "close")
    preclose_d = _decimal(preclose, "preclose")
    if preclose_d <= 0:
        raise DailyFactsV02Error("V02_INVALID_PRECLOSE", "preclose is not positive")
    return float((close_d / preclose_d - 1) * 100)


def derive_turnover_rate(volume_shares: Any, float_shares: Any) -> float:
    """Turnover in percent points: traded shares over the day's float shares.

    Both operands are in shares. ``cnequity.domain.units`` fixes the lake's
    volume unit as shares and converts every vendor at its adapter boundary,
    so no lot factor belongs here.
    """
    volume = _decimal(volume_shares, "volume")
    floating = _decimal(float_shares, "float_shares")
    if floating <= 0:
        raise DailyFactsV02Error("V02_INVALID_FLOAT_SHARES", "float shares are not positive")
    return float(volume / floating * 100)


def risk_warning_to_is_st(value: Any) -> str:
    """Nullable risk_warning Boolean -> ASL tri-state is_st."""
    if value is None:
        return UNKNOWN
    return "TRUE" if bool(value) else "FALSE"


@dataclass(frozen=True)
class FloatSharesPoint:
    symbol: str
    change_date: date
    announce_date: date | None
    float_shares: float
    source: str


def pit_float_shares(points: Sequence[FloatSharesPoint], trade_date: date) -> FloatSharesPoint | None:
    """Latest share-structure point that was public AND effective on the date.

    Both conditions are required. A restructuring can take effect before it is
    disclosed, and a denominator the market could not yet see is not
    point-in-time, so ``announce_date`` is as load-bearing as ``change_date``.
    """
    eligible = [
        point
        for point in points
        if point.change_date <= trade_date
        and (point.announce_date is not None and point.announce_date <= trade_date)
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda point: (point.change_date, point.announce_date or trade_date))


def index_float_shares(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[FloatSharesPoint]]:
    """Group share-structure rows by symbol, dropping unusable records.

    A row with no ``announce_date`` cannot satisfy strict PIT and is dropped
    rather than treated as always-available: assuming availability is exactly
    the look-ahead this contract forbids."""
    indexed: dict[str, list[FloatSharesPoint]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "")
        change_date = _as_date(row.get("change_date"))
        announce_date = _as_date(row.get("announce_date"))
        value = row.get("float_shares")
        if not symbol or change_date is None or announce_date is None or value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric) or numeric <= 0:
            continue
        indexed.setdefault(symbol, []).append(
            FloatSharesPoint(symbol, change_date, announce_date, numeric, str(row.get("source") or ""))
        )
    return indexed


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None



def assert_source_independent(paths: Iterable[Path]) -> None:
    """Refuse any input path that belongs to the frozen V1 evidence."""
    for path in paths:
        text = str(path).replace("\\\\", "/")
        for token in FORBIDDEN_PATH_TOKENS:
            if token in text:
                raise DailyFactsV02Error(
                    "V02_SOURCE_DEPENDENCE_VIOLATION",
                    f"candidate generation may not read {token!r}: {path}",
                )


@dataclass(frozen=True)
class SourceBundle:
    """Read-only view of every admissible V02 input."""

    root: Path
    r3_manifest_hash: str
    r3_files: tuple[str, ...]
    trading_status: Mapping[tuple[str, str], Mapping[str, Any]]
    trading_status_files: tuple[str, ...]
    corporate_actions: Mapping[tuple[str, str], Mapping[str, Any]]
    corporate_actions_files: tuple[str, ...]
    share_structure: Mapping[str, Sequence[FloatSharesPoint]]
    share_structure_files: tuple[str, ...]
    valuation_metrics: Mapping[tuple[str, str], float]
    valuation_files: tuple[str, ...]
    reference_price_evidence: Mapping[tuple[str, str], CashDividendReferenceEvidence]

    def provenance(self) -> dict[str, Any]:
        return {
            "r3_daily_manifest_hash": self.r3_manifest_hash,
            "r3_daily_file_n": len(self.r3_files),
            "trading_status_file_n": len(self.trading_status_files),
            "trading_status_row_n": len(self.trading_status),
            "corporate_actions_file_n": len(self.corporate_actions_files),
            "corporate_actions_row_n": len(self.corporate_actions),
            "share_structure_file_n": len(self.share_structure_files),
            "share_structure_symbol_n": len(self.share_structure),
            "valuation_metrics_file_n": len(self.valuation_files),
            "valuation_metrics_row_n": len(self.valuation_metrics),
            "reference_price_evidence_n": len(self.reference_price_evidence),
        }


def _published_r3(root: Path) -> tuple[str, tuple[str, ...]]:
    """The pointer-verified R3 file universe, never a physical glob."""
    pointer = json.loads((root / "meta/asl/r3/published-daily-authority.json").read_text())
    manifest = json.loads((root / pointer["plan"]).read_text())["EXPECTED_POST_INPUT_MANIFEST"]
    files = tuple(item["relative_path"] for item in manifest["FILES"])
    if canonical_sha(manifest["FILES"]) != pointer["manifest_hash"]:
        raise DailyFactsV02Error("V02_R3_MANIFEST_DRIFT", "published R3 manifest hash mismatch")
    return str(pointer["manifest_hash"]), files


def _load_rows(paths: Sequence[Path]) -> list[dict[str, Any]]:
    import polars as pl

    if not paths:
        return []
    return pl.read_parquet([str(path) for path in paths]).to_dicts()


def load_sources(root: Path, *, evidence_path: Path | None = None) -> SourceBundle:
    """Load every admissible input, refusing anything from the V1 evidence."""
    root = Path(root)
    manifest_hash, r3_files = _published_r3(root)

    def dataset_files(name: str) -> list[Path]:
        base = root / "curated" / name
        files = sorted(base.rglob("*.parquet")) if base.is_dir() else []
        assert_source_independent(files)
        return files

    status_files = dataset_files("trading_status")
    action_files = dataset_files("corporate_actions")
    structure_files = dataset_files("share_structure")
    valuation_files = dataset_files("valuation_metrics")

    status: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in _load_rows(status_files):
        status[(str(row.get("symbol")), str(_as_date(row.get("trade_date"))))] = row

    actions: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in _load_rows(action_files):
        actions[(str(row.get("symbol")), str(_as_date(row.get("ex_date"))))] = row

    structures = index_float_shares(_load_rows(structure_files))

    valuation: dict[tuple[str, str], float] = {}
    for row in _load_rows(valuation_files):
        value = row.get("float_mv")
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            valuation[(str(row.get("symbol")), str(_as_date(row.get("trade_date"))))] = numeric

    evidence: dict[tuple[str, str], CashDividendReferenceEvidence] = {}
    if evidence_path is not None:
        evidence_path = Path(evidence_path)
        # The official-disclosure directory is deliberately excluded from the
        # admissible set above, but the certified exception document is an
        # explicit, contract-named input; it is loaded by exact path and its
        # own hash-verified records, never by scanning the V1 tree.
        if evidence_path.is_file():
            try:
                evidence = dict(load_reference_price_evidence(root, evidence_path))
            except ReferencePriceEvidenceError as exc:
                raise DailyFactsV02Error("V02_REFERENCE_PRICE_EVIDENCE_INVALID", exc.code) from exc

    return SourceBundle(
        root=root,
        r3_manifest_hash=manifest_hash,
        r3_files=r3_files,
        trading_status=status,
        trading_status_files=tuple(str(p) for p in status_files),
        corporate_actions=actions,
        corporate_actions_files=tuple(str(p) for p in action_files),
        share_structure=structures,
        share_structure_files=tuple(str(p) for p in structure_files),
        valuation_metrics=valuation,
        valuation_files=tuple(str(p) for p in valuation_files),
        reference_price_evidence=evidence,
    )


def r3_bars(bundle: SourceBundle, day: str) -> dict[str, dict[str, Any]]:
    """Rows of the published R3 partition for one trade date.

    The path is resolved through the pointer-verified manifest, not a glob, so
    an unpublished partition can never become a V02 input."""
    import polars as pl

    relative = f"curated/daily_bars/trade_date={day}/part-merged.parquet"
    if relative not in bundle.r3_files:
        raise DailyFactsV02Error("V02_R3_PARTITION_ABSENT", relative)
    frame = pl.read_parquet(bundle.root / relative)
    return {str(row["symbol"]): row for row in frame.to_dicts()}


def prior_session(day: str, *, calendar: Sequence[date]) -> str | None:
    """Trading date immediately before ``day``, from the persisted calendar."""
    target = date.fromisoformat(day)
    earlier = [value for value in calendar if value < target]
    return max(earlier).isoformat() if earlier else None



@dataclass(frozen=True)
class LifecycleRecord:
    """The formal listed lifecycle a key must fall inside."""

    symbol: str
    list_date: date | None
    delist_date: date | None

    def covers(self, trade_date: date) -> bool:
        if self.list_date is not None and trade_date < self.list_date:
            return False
        if self.delist_date is not None and trade_date >= self.delist_date:
            return False
        return True


def build_shadow_rows(
    bundle: SourceBundle,
    day: str,
    prior_day: str | None,
    *,
    lifecycle: Mapping[str, LifecycleRecord] | None = None,
    prior_days: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Derive one V02 fact row per published R3 key on ``day``.

    Returns ``(rows, report)``. The report carries the Decision A guard
    outcome: any ``delisted`` status inside the frozen eligible universe is a
    certification blocker, never a row value.
    """
    bars = r3_bars(bundle, day)
    # The prior close is the last *observed* close, not merely the previous
    # calendar session: the R3 authority encodes a suspension as a row that
    # may be absent, so walking back is what keeps a resumption's reference
    # price on the same basis the exchange uses.
    candidates = list(prior_days) if prior_days else ([prior_day] if prior_day else [])
    prior: dict[str, float] = {}
    prior_used: dict[str, str] = {}
    for candidate_day in candidates:
        if candidate_day is None or candidate_day == day:
            continue
        try:
            older = r3_bars(bundle, candidate_day)
        except DailyFactsV02Error:
            continue
        for symbol, row in older.items():
            close_value = row.get("close")
            if symbol in prior or not isinstance(close_value, (int, float)):
                continue
            prior[symbol] = float(close_value)
            prior_used[symbol] = candidate_day
    target = date.fromisoformat(day)

    rows: list[dict[str, Any]] = []
    lifecycle_conflicts: list[str] = []
    unknown_status: list[str] = []
    missing_prior: list[str] = []
    turnover_unresolved: list[str] = []

    for symbol in sorted(bars):
        bar = bars[symbol]
        prior_close = prior.get(symbol)
        volume = bar.get("volume")
        close = bar.get("close")

        status_row = bundle.trading_status.get((symbol, day))
        status_value = str(status_row.get("status")) if status_row else None
        risk_warning = status_row.get("risk_warning") if status_row else None

        # ---- Decision A: delisted is a lifecycle fact, not a daily value ---
        if status_value == "delisted":
            record = (lifecycle or {}).get(symbol)
            inside = record.covers(target) if record is not None else False
            if inside or record is None:
                # Inside the frozen eligible universe a delisted status is a
                # contradiction between two authorities; fail closed.
                lifecycle_conflicts.append(symbol)
            continue

        row: dict[str, Any] = {"symbol": symbol, "trade_date": day, "schema_version": SCHEMA}
        blockers: list[str] = []

        # ---- trade_status / is_st ------------------------------------------
        if status_value is None:
            trade_status = UNKNOWN
            unknown_status.append(symbol)
            blockers.append("TRADING_STATUS_EVIDENCE_MISSING")
        else:
            trade_status = STATUS_TO_TRADE_STATUS.get(status_value, UNKNOWN)
            if trade_status == UNKNOWN:
                unknown_status.append(symbol)
                blockers.append("TRADING_STATUS_VOCABULARY_UNKNOWN")
        is_st = risk_warning_to_is_st(risk_warning)
        if is_st == UNKNOWN:
            blockers.append("RISK_WARNING_UNKNOWN")
        row["trade_status"] = trade_status
        row["is_st"] = is_st
        row["status_source"] = RULE_STATUS_DATASET if status_row else None
        row["status_dataset_value"] = status_value

        # ---- preclose -------------------------------------------------------
        evidence = bundle.reference_price_evidence.get((symbol, day))
        if evidence is not None:
            if prior_close is None:
                missing_prior.append(symbol)
                blockers.append("NO_PRIOR_SESSION_CLOSE")
                row["preclose"] = None
                row["preclose_rule_id"] = None
                row["preclose_source"] = SOURCE_REFERENCE_PRICE
            else:
                action = bundle.corporate_actions.get((symbol, day)) or {}
                reference = display_reference_price(
                    prior_close,
                    cash_dividend=evidence.effective_cash_dividend_per_share,
                    bonus_ratio=action.get("bonus_ratio") or 0,
                    transfer_ratio=action.get("transfer_ratio") or 0,
                    allotment_ratio=action.get("allotment_ratio") or 0,
                    allotment_price=action.get("allotment_price") or 0,
                )
                row["preclose"] = float(reference)
                row["preclose_rule_id"] = RULE_PRECLOSE_REFERENCE_PRICE
                row["preclose_source"] = SOURCE_REFERENCE_PRICE
                row["reference_price_expected"] = float(reference)
                row["reference_price_announcement_id"] = evidence.announcement_id
                row["reference_price_source_hash"] = evidence.source_hash
        elif prior_close is not None:
            row["preclose"] = float(prior_close)
            row["preclose_rule_id"] = RULE_PRECLOSE_PRIOR_CLOSE
            row["preclose_source"] = SOURCE_PRIOR_R3_CLOSE
            row["prior_close_session"] = prior_used.get(symbol)
        else:
            missing_prior.append(symbol)
            blockers.append("NO_PRIOR_SESSION_CLOSE")
            row["preclose"] = None
            row["preclose_rule_id"] = None
            row["preclose_source"] = None

        # ---- pct_chg: local derivation, never a provider field --------------
        if trade_status == SUSPENDED:
            # A halted session has no traded move; the frozen V1 semantics
            # publish null here rather than a derived 0.0, which would claim
            # a flat close the market never printed.
            row["pct_chg"] = None
            row["pct_chg_rule_id"] = None
            row["pct_chg_null_reason"] = "SUSPENDED"
        elif row["preclose"] is not None and close is not None:
            row["pct_chg"] = derive_pct_chg(close, row["preclose"])
            row["pct_chg_rule_id"] = RULE_PCT_CHG
        else:
            row["pct_chg"] = None
            row["pct_chg_rule_id"] = None
            blockers.append("PCT_CHG_UNDERIVABLE")

        # ---- turnover_rate: strict PIT denominator --------------------------
        point = pit_float_shares(bundle.share_structure.get(symbol, ()), target)
        if point is None:
            row["turnover_rate"] = None
            row["turnover_rate_rule_id"] = None
            row["float_shares"] = None
            turnover_unresolved.append(symbol)
            blockers.append("TURNOVER_UNRESOLVED")
        else:
            row["float_shares"] = point.float_shares
            row["float_shares_change_date"] = point.change_date.isoformat()
            row["float_shares_announce_date"] = point.announce_date.isoformat() if point.announce_date else None
            row["float_shares_source"] = point.source
            # A suspended session keeps the V1 semantics: null, never 0.
            if trade_status == SUSPENDED:
                row["turnover_rate"] = None
                row["turnover_rate_rule_id"] = None
                row["turnover_null_reason"] = "SUSPENDED"
            elif isinstance(volume, (int, float)):
                row["turnover_rate"] = derive_turnover_rate(volume, point.float_shares)
                row["turnover_rate_rule_id"] = RULE_TURNOVER
            else:
                row["turnover_rate"] = None
                row["turnover_rate_rule_id"] = None
                blockers.append("VOLUME_MISSING")
        row["turnover_unit"] = "PERCENT"

        # ---- crosscheck evidence only, never a canonical denominator --------
        float_mv = bundle.valuation_metrics.get((symbol, day))
        if float_mv is not None and row.get("float_shares") and close is not None:
            row["valuation_float_mv"] = float_mv
            row["float_shares_times_close"] = float(
                _decimal(row["float_shares"], "float_shares") * _decimal(close, "close")
            )

        row["quality_status"] = "PASS" if not blockers else "UNKNOWN"
        row["blockers"] = ",".join(sorted(set(blockers))) or None
        rows.append(row)

    report = {
        "eligible_n": len(bars),
        "row_n": len(rows),
        "excluded_lifecycle_n": len(bars) - len(rows),
        "ELIGIBILITY_LIFECYCLE_CONFLICT_N": len(lifecycle_conflicts),
        "ELIGIBILITY_LIFECYCLE_CONFLICT_SYMBOLS": sorted(lifecycle_conflicts),
        "TRADING_STATUS_UNKNOWN_N": len(unknown_status),
        "MISSING_PRIOR_SESSION_N": len(missing_prior),
        "TURNOVER_UNRESOLVED_N": len(turnover_unresolved),
        "UNKNOWN_N": sum(1 for row in rows if row["trade_status"] == UNKNOWN or row["is_st"] == UNKNOWN),
        "prior_session": prior_day,
    }
    return rows, report
