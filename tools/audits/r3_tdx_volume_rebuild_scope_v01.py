#!/usr/bin/env python3
"""R3 TDX volume rebuild-scope offline classifier (research-only, V01.1).

Classifies every existing canonical TDX security daily volume row into
PROVABLY_UNAFFECTED / PROVABLY_AFFECTED / AMBIGUOUS using ONLY the retained
decoded value (canonical = int(old_get_volume(raw))*100), with no raw wire
bytes and no network.

V01.1 corrections (Sol audit blockers closed)
============================================

A. Normal band floor corrected from 256 to 128 (re-proven, not handwaved):
   lp >= 0x43 implies old_get_volume(raw) == IEEE-754 float32(raw) EXACTLY
   (derivation below), and lp=0x43 starts at 0x43000000 = 128.0 lots.  The
   integer preimages 128..255 therefore also have a normal-band raw with
   IEEE == old_int, so they can never be PROVABLY_AFFECTED.

B. Candidate-set classification only.  Economic plausibility (amount VWAP
   vs [low, high]) is never allowed to change a mathematical class.  It is
   reported only as SUPPORTING_DIAGNOSTIC.

C. TDX_AMOUNT_GLOBAL_CORRECTNESS is UNPROVEN: retained amount is the full-
   precision float64 output of the SAME old get_volume() path and no raw
   amount bytes are retained, so the decoder path of historical amount
   cannot be proven from retained data alone.

D. The rebuild decision never returns OFFLINE_DETERMINISTIC_REBUILD_POSSIBLE
   while AMBIGUOUS_N > 0.

Valid raw domain (formal)
=========================

Canonical volume = int(old_get_volume(raw)) * 100 shares (pinned
``lots_to_shares``).  Minimum positive volume is 1 share, therefore the
minimum positive native quantity is 0.01 lots.  The wire raw is the IEEE-754
float32 encoding of that native quantity, rounded to nearest; rounding is
monotone, so every valid positive raw satisfies

    ieee754_float32(raw) >= RN(0.01)   (RN = round-to-nearest float32)

which is exactly 0x3C23D70A = 0.009999999776482582.  Any raw below that is
strictly less than 1 share and cannot be the encoding of a positive integer
share volume; in particular every lp < 0x3C is excluded (its IEEE value is
below 2^-7 lots = 0.78125 shares).

Decoder equality theorem
========================

For every raw with lp = byte 3 >= 0x43:

- dw_edx = 2*lp - 0x86 >= 0, so the reciprocal branch of get_volume is never
  taken;
- hleax <= 0x7F:  old = 2^(2lp-0x7F) * (1 + hleax/2^7 + lheax/2^15 +
  lleax/2^23) == IEEE (b23 = 0);
- hleax == 0x80:  old = 2^(2lp-0x7E) * (1 + lheax/2^15 + lleax/2^23) == IEEE
  (b23 = 1, mantissa zero high bit);
- hleax >= 0x81:  old = 2^(2lp-0x7F) * (2 + (hleax&0x7F)/2^6 + lheax/2^14 +
  lleax/2^21) == IEEE (b23 = 1).

Hence int(OLD) == int(NEW) for every raw with lp >= 0x43 (verified
exhaustively for lp 0x43..0x47 in V01; the equality above is exact for all
lp >= 0x43 and also exhaustively verified over the full working range
0x43..0x4D in the V01.1 scan).

The divergence band is exactly lp in 0x3C..0x42, enumerated over the full
24-bit mantissa in the preimage tables below.

Complete preimage model
=======================

For a retained old_int k the candidate set over the COMPLETE valid raw
domain is:

- every anomaly-band raw (lp 0x3C..0x42, valid domain) with
  int(old(raw)) == k contributes corrected candidate int(ieee(raw));
- if k >= 128, the normal band (lp >= 0x43) is dense in [k, k+1) subject to
  float32 representability: a raw v exists with int(v) == k iff the integer
  k is representable (k < 2^24, or k % 2^(bit_length(k)-1-23) == 0); every
  such raw is a candidate with corrected value exactly k.

Classification (pure candidate-set, no plausibility override):

- PROVABLY_UNAFFECTED: every valid candidate yields the retained k.
- PROVABLY_AFFECTED: every valid candidate yields the same corrected value
  c != k.
- AMBIGUOUS: the candidate set is empty (model gap -> fail closed) or
  contains more than one distinct value.

V01 legacy implementation is kept (table built without the domain filter,
floor 256, amount-VWAP row override) solely to diff V01 vs V01.1
classifications per row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import polars as pl
from cnequity.adapters.tdx_protocol._wire.helper import get_volume

sys.path.insert(0, str(Path(__file__).resolve().parent))
from r3_tdx_volume_anomaly_audit_v01 import hard_anomaly_mask  # noqa: E402

DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")

SHARES_PER_LOT = 100
MIN_POSITIVE_VOLUME_SHARES = 1
MIN_POSITIVE_NATIVE_LOTS = MIN_POSITIVE_VOLUME_SHARES / SHARES_PER_LOT  # 0.01

# IEEE-754 float32 round-to-nearest of 0.01 lots (the smallest stored value
# that can encode a positive integer share volume).
_VALID_MIN_RAW = struct.unpack("<I", struct.pack("<f", MIN_POSITIVE_NATIVE_LOTS))[0]
VALID_MIN_QUANTITY = struct.unpack("<f", struct.pack("<f", MIN_POSITIVE_NATIVE_LOTS))[0]

DIVERGENCE_LP_MIN = 0x3C
DIVERGENCE_LP_MAX = 0x42
NORMAL_BAND_MIN_LP = 0x43
NORMAL_BAND_FLOOR = 128  # 2**7: 0x43000000 == 128.0 lots, re-proven boundary
MAX_REAL_LP = 0x7F  # complete positive-finite float32 high-byte ceiling


def ieee754_float32(raw: int) -> float:
    return struct.unpack("<f", struct.pack("<I", raw & 0xFFFFFFFF))[0]


def is_valid_quantity_raw(raw: int) -> bool:
    """Valid positive stock daily-volume raw domain: ieee >= RN(0.01 lots)."""
    return ieee754_float32(raw) >= VALID_MIN_QUANTITY


def valid_raw_domain_bounds() -> dict[str, Any]:
    """Formal domain proof bundle (no handwave)."""
    return {
        "MIN_POSITIVE_VOLUME_SHARES": MIN_POSITIVE_VOLUME_SHARES,
        "MIN_POSITIVE_NATIVE_LOTS": MIN_POSITIVE_NATIVE_LOTS,
        "VALID_MIN_QUANTITY_LOTS": VALID_MIN_QUANTITY,
        "VALID_MIN_RAW_HEX": f"0x{_VALID_MIN_RAW:08X}",
        "LP_RANGE": [f"0x{DIVERGENCE_LP_MIN:02X}", f"0x{MAX_REAL_LP:02X}"],
        "LP_BELOW_3C_EXCLUDED": (
            "every lp < 0x3C decodes to an IEEE value below 2^-7 lots "
            "(0.78125 shares) < 1 share; rounding is monotone so no "
            "positive integer share volume can be encoded there"
        ),
        "NORMAL_BAND_FLOOR": NORMAL_BAND_FLOOR,
        "NORMAL_BAND_FLOOR_PROOF": (
            "lp >= 0x43 => old == IEEE exactly (theorem in module docstring); "
            "lp = 0x43 begins at 0x43000000 = 128.0 lots = 2^7, hence integer "
            "preimages 128..255 also have a normal-band raw with IEEE == old_int"
        ),
    }


def normal_band_raw_candidate(old_int: int) -> int | None:
    """An explicit normal-band raw whose IEEE value int-truncates to old_int.

    For representable old_int >= 128 the exact float32 encoding of the
    integer is itself a normal-band raw (its exponent >= 134, lp >= 0x43):
    128 -> 0x43000000, 156 -> 0x431C0000, 208 -> 0x43500000,
    255 -> 0x437F0000, 256 -> 0x43800000.
    """
    if old_int < NORMAL_BAND_FLOOR or not _float32_int_reachable(old_int):
        return None
    return struct.unpack("<I", struct.pack("<f", float(old_int)))[0]


def _float32_int_reachable(k: int) -> bool:
    """Is there any float32 v with int(v) == k (v in [k, k+1) intersect f32)?"""
    if k <= 0:
        return False
    if k < 1 << 24:
        return True  # every integer < 2^24 is exactly representable (v == k)
    e = k.bit_length() - 1
    t = (k & -k).bit_length() - 1  # trailing zero count
    return (e - t) <= 23


def normal_candidate_exists(old_int: int) -> bool:
    return old_int >= NORMAL_BAND_FLOOR and _float32_int_reachable(old_int)


def _build_preimage_tables() -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    """old_int -> set(int(ieee(raw))) over full mantissa, divergence band.

    Returns (domain-filtered table, V01-legacy unfiltered table) built in a
    single enumeration.
    """
    table_domain: dict[int, set[int]] = defaultdict(set)
    table_full: dict[int, set[int]] = defaultdict(set)
    for lp in range(DIVERGENCE_LP_MIN, DIVERGENCE_LP_MAX + 1):
        base = lp << 24
        for m in range(0x1000000):
            raw = base | m
            o = int(get_volume(raw))
            if o <= 0:
                continue
            n = int(ieee754_float32(raw))
            table_full[o].add(n)
            if is_valid_quantity_raw(raw):
                table_domain[o].add(n)
    return dict(table_domain), dict(table_full)


_TABLES: tuple[dict[int, set[int]], dict[int, set[int]]] | None = None


def _tables() -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    global _TABLES
    if _TABLES is None:
        _TABLES = _build_preimage_tables()
    return _TABLES


def divide_into_candidates(
    old_int: int, anomaly_cands: set[int] | None = None
) -> tuple[str, int | None]:
    """Pure candidate-set classification.

    anomaly_cands: the divergence-band candidate set for old_int (already
    domain-filtered).  None -> global table is consulted.
    """
    if anomaly_cands is None:
        anomaly_cands = _tables()[0].get(old_int, set())
    cands: set[int] = set(anomaly_cands)
    if normal_candidate_exists(old_int):
        cands.add(old_int)
    if not cands:
        # Model gap: retained row must have had some raw; fail closed rather
        # than guess.
        return "AMBIGUOUS", None
    if all(c == old_int for c in cands):
        return "PROVABLY_UNAFFECTED", old_int
    if len(cands) == 1:
        (val,) = cands
        if val != old_int:
            return "PROVABLY_AFFECTED", val
    return "AMBIGUOUS", None


def classify_old_int(old_int: int) -> tuple[str, int | None]:
    """V01.1 pure classification of one canonical//100 value."""
    return divide_into_candidates(old_int)


def classify_old_int_v01(old_int: int) -> tuple[str, int | None]:
    """V01 legacy math classification (floor 256, unfiltered divergence table)."""
    return classify_old_int_v01_with(old_int, _tables()[1].get(old_int, set()))


def classify_old_int_v01_with(
    old_int: int, anomaly_cands: set[int]
) -> tuple[str, int | None]:
    """V01 legacy math classification with an explicit candidate set (tests)."""
    if anomaly_cands:
        cands = anomaly_cands
        if len(cands) == 1:
            (val,) = cands
            if val == old_int:
                return "PROVABLY_UNAFFECTED", val
            if old_int < 256:
                return "PROVABLY_AFFECTED", val
            return "AMBIGUOUS", None
        return "AMBIGUOUS", None
    if old_int >= 256:
        return "PROVABLY_UNAFFECTED", old_int
    return "AMBIGUOUS", None


def classify_row_v01(
    *,
    old_int: int,
    amount: float,
    low: float,
    high: float,
) -> tuple[str, int | None]:
    """V01 legacy ROW classification, including the removed amount-VWAP
    override.  Kept only to diff V01 vs V01.1 per row."""
    cls, corr = classify_old_int_v01(old_int)
    return _row_v01_apply_vwap(cls, corr, old_int, amount, low, high)


def _row_v01_apply_vwap(
    cls: str,
    corr: int | None,
    old_int: int,
    amount: float,
    low: float,
    high: float,
) -> tuple[str, int | None]:
    """Shared V01 row-level amount-VWAP override (legacy, kept for diff)."""
    if cls == "PROVABLY_AFFECTED":
        old_vwap = amount / (old_int * SHARES_PER_LOT)
        old_plausible = low <= old_vwap <= high
        if old_plausible:
            return "PROVABLY_UNAFFECTED", old_int
        corr_vwap = amount / (corr * SHARES_PER_LOT)
        if low <= corr_vwap <= high:
            return "PROVABLY_AFFECTED", corr
        return "AMBIGUOUS", None
    if cls == "PROVABLY_UNAFFECTED":
        return cls, old_int
    return cls, None


def classify_row_v01_with(
    *,
    old_int: int,
    amount: float,
    low: float,
    high: float,
    anomaly_cands: set[int],
) -> tuple[str, int | None]:
    """V01 legacy ROW classification with an explicit candidate set (tests)."""
    cls, corr = classify_old_int_v01_with(old_int, anomaly_cands)
    return _row_v01_apply_vwap(cls, corr, old_int, amount, low, high)


def amount_supporting_diagnostic(
    *,
    old_int: int,
    corrected: int | None,
    amount: float,
    low: float,
    high: float,
) -> dict[str, Any]:
    """Non-classifying amount/OHLC diagnostic (SUPPORTING_DIAGNOSTIC only)."""
    diag: dict[str, Any] = {
        "RETAINED_VWAP": amount / (old_int * SHARES_PER_LOT),
        "RETAINED_VWAP_IN_RANGE": low <= amount / (old_int * SHARES_PER_LOT) <= high,
    }
    if corrected is not None and corrected > 0:
        corr_vwap = amount / (corrected * SHARES_PER_LOT)
        diag["CORRECTED_VWAP"] = corr_vwap
        diag["CORRECTED_VWAP_IN_RANGE"] = low <= corr_vwap <= high
    else:
        diag["CORRECTED_VWAP"] = None
        diag["CORRECTED_VWAP_IN_RANGE"] = None
    return diag


def amount_global_correctness() -> dict[str, Any]:
    """Historical TDX daily amount decoder-path gate (offline, no raw bytes)."""
    return {
        "TDX_AMOUNT_GLOBAL_CORRECTNESS": "UNPROVEN",
        "note": (
            "retained amount is the untruncated float64 output of the SAME "
            "old get_volume() decoder on raw amount bytes that are not "
            "retained; without raw bytes the amount decoder path cannot be "
            "proven correct or defective from retained data alone.  Amount "
            "and OHLC are reported only as SUPPORTING_DIAGNOSTIC and never "
            "change AMBIGUOUS -> UNAFFECTED / AFFECTED."
        ),
    }


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def manifest_byte_form(rows: list[dict[str, Any]]) -> bytes:
    """Deterministic canonical serialization for key manifests."""
    return json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_input_file_manifest(data_root: Path) -> dict[str, Any]:
    """Deterministic read-only manifest of every curated daily_bars parquet."""
    base = data_root / "curated" / "daily_bars"
    files = sorted(base.rglob("*.parquet"))
    rows: list[dict[str, Any]] = []
    for p in files:
        digest = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        rows.append(
            {
                "relative_path": str(p.relative_to(data_root)),
                "file_size": p.stat().st_size,
                "sha256": digest.hexdigest(),
            }
        )
    payload = manifest_byte_form(rows)
    return {
        "INPUT_FILE_N": len(rows),
        "INPUT_MANIFEST_HASH": _hash_bytes(payload),
        "CANONICAL_SERIALIZATION": (
            "json.dumps(rows, ensure_ascii=True, sort_keys=True, "
            "separators=(',', ':')) sorted by relative_path"
        ),
        "FILES": rows,
    }


def write_target_manifest(target_rows: list[dict[str, Any]], out: Path) -> dict[str, Any]:
    """Persist the complete non-unaffected key manifest with hashes."""
    affected = [r for r in target_rows if r["classification"] == "PROVABLY_AFFECTED"]
    ambiguous = [r for r in target_rows if r["classification"] == "AMBIGUOUS"]
    affected_bytes = manifest_byte_form(affected)
    ambiguous_bytes = manifest_byte_form(ambiguous)
    superset_bytes = manifest_byte_form(target_rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(superset_bytes)
    return {
        "AFFECTED_KEY_N": len(affected),
        "AMBIGUOUS_KEY_N": len(ambiguous),
        "REPAIR_SUPERSET_KEY_N": len(target_rows),
        "AFFECTED_KEY_HASH": _hash_bytes(affected_bytes),
        "AMBIGUOUS_KEY_HASH": _hash_bytes(ambiguous_bytes),
        "REPAIR_SUPERSET_KEY_HASH": _hash_bytes(superset_bytes),
        "TARGET_MANIFEST_FILE": str(out),
        "CANONICAL_SERIALIZATION": (
            "json.dumps(rows, ensure_ascii=True, sort_keys=True, "
            "separators=(',', ':')) sorted by (symbol, trade_date)"
        ),
    }


def scan_canonical(data_root: Path) -> dict[str, Any]:
    df = pl.read_parquet(str(data_root / "curated/daily_bars/**/*.parquet"))
    tdx = df.filter(
        (pl.col("source") == "tdx_protocol")
        & (pl.col("volume") > 0)
        & pl.col("volume").is_finite()
    )
    rem = tdx.with_columns((pl.col("volume") % SHARES_PER_LOT).alias("rem"))
    non_lot_multiple = int(rem.filter(pl.col("rem") != 0).height)
    if non_lot_multiple:
        raise AssertionError(
            f"{non_lot_multiple} tdx rows not divisible by {SHARES_PER_LOT}"
        )

    tables = _tables()

    # HARD set from the frozen V01 anomaly contract (amount>0 + finite OHLC).
    hard_input = tdx.filter(
        (pl.col("amount") > 0)
        & pl.col("open").is_finite()
        & pl.col("high").is_finite()
        & pl.col("low").is_finite()
        & pl.col("close").is_finite()
        & pl.col("amount").is_finite()
    )
    hard_df = hard_input.with_columns(hard_anomaly_mask(hard_input).alias("hard"))

    hard_keys = {
        (str(r["symbol"]), r["trade_date"])
        for r in hard_df.filter(pl.col("hard")).select(["symbol", "trade_date"])
        .iter_rows(named=True)
    }

    counts = {
        "PROVABLY_AFFECTED": 0,
        "PROVABLY_UNAFFECTED": 0,
        "AMBIGUOUS": 0,
    }
    affected_symbols: set[str] = set()
    ambiguous_symbols: set[str] = set()
    affected_keys: list[tuple[str, Any]] = []
    ambiguous_keys: list[tuple[str, Any]] = []
    target_key_rows: list[dict[str, Any]] = []
    corrected_map: dict[int, int] = {}
    transitions: dict[tuple[str, str], int] = defaultdict(int)
    vwap_override_unaffected_n = 0
    floor128_affected_n = 0
    supporting = {"affected_corrected_vwap_in_range_n": 0, "affected_n_with_diag": 0}
    hard_affected = 0
    hard_ambiguous = 0
    hard_unaffected = 0

    for row in tdx.iter_rows(named=True):
        symbol = str(row["symbol"])
        trade_date = row["trade_date"]
        old_int = int(row["volume"]) // SHARES_PER_LOT
        amount = float(row["amount"])
        low = float(row["low"])
        high = float(row["high"])

        cls, corr = divide_into_candidates(old_int)
        v01_cls, v01_corr = classify_row_v01(
            old_int=old_int, amount=amount, low=low, high=high
        )
        transitions[(v01_cls, cls)] += 1

        if v01_cls != cls:
            # attribute the change for the report
            if v01_cls == "PROVABLY_AFFECTED" and cls == "AMBIGUOUS":
                if NORMAL_BAND_FLOOR <= old_int <= 255:
                    floor128_affected_n += 1
            if v01_cls == "PROVABLY_UNAFFECTED" and cls != "PROVABLY_UNAFFECTED":
                vwap_override_unaffected_n += 1

        counts[cls] += 1
        if cls == "PROVABLY_AFFECTED":
            affected_symbols.add(symbol)
            affected_keys.append((symbol, trade_date))
            corrected_map[old_int] = corr
            target_key_rows.append(
                {
                    "symbol": symbol,
                    "trade_date": trade_date.isoformat(),
                    "current_volume": int(row["volume"]),
                    "classification": cls,
                    "corrected_volume_if_provably_affected": int(
                        corr * SHARES_PER_LOT
                    ),
                    "source": "tdx_protocol",
                }
            )
            d = amount_supporting_diagnostic(
                old_int=old_int, corrected=corr, amount=amount, low=low, high=high
            )
            if d.get("CORRECTED_VWAP_IN_RANGE"):
                supporting["affected_corrected_vwap_in_range_n"] += 1
            supporting["affected_n_with_diag"] += 1
        elif cls == "AMBIGUOUS":
            ambiguous_symbols.add(symbol)
            ambiguous_keys.append((symbol, trade_date))
            target_key_rows.append(
                {
                    "symbol": symbol,
                    "trade_date": trade_date.isoformat(),
                    "current_volume": int(row["volume"]),
                    "classification": cls,
                    "corrected_volume_if_provably_affected": None,
                    "source": "tdx_protocol",
                }
            )

        key = (symbol, trade_date)
        if key in hard_keys:
            if cls == "PROVABLY_AFFECTED":
                hard_affected += 1
            elif cls == "AMBIGUOUS":
                hard_ambiguous += 1
            else:
                hard_unaffected += 1

    total = sum(counts.values())
    assert total == int(tdx.height), (total, tdx.height)

    def _span(keys: list[tuple[str, Any]], sample: int = 20) -> dict[str, Any]:
        by_sym: dict[str, list[Any]] = defaultdict(list)
        for sym, d in keys:
            by_sym[sym].append(d)
        spans = {
            s: (max(v) - min(v)).days
            for s, v in by_sym.items()
        }
        span_stats: dict[str, Any] = {"SYMBOL_N": len(spans), "KEY_N": len(keys)}
        if spans:
            ordered = sorted(spans.values())
            n = len(ordered)
            span_stats["MIN_DAYS"] = ordered[0]
            span_stats["MEDIAN_DAYS"] = ordered[n // 2]
            span_stats["MAX_DAYS"] = ordered[-1]
            span_stats["SAMPLE"] = [
                {"SYMBOL": s, "KEY_N": len(v)}
                for s, v in sorted(by_sym.items())[:sample]
            ]
        return {
            "KEY_N": len(keys),
            "SYMBOL_N": len(by_sym),
            "SPAN_STATS": span_stats,
        }

    ambiguous_plan = _span(ambiguous_keys)
    affected_plan = _span(affected_keys)

    # Engineering cost shapes, NO execution.
    tdx_pages = 0
    # recompute per-symbol spans for the cost shapes (not persisted in full)
    amb_by_sym: dict[str, list[Any]] = defaultdict(list)
    for sym, d in ambiguous_keys:
        amb_by_sym[sym].append(d)
    aff_by_sym: dict[str, list[Any]] = defaultdict(list)
    for sym, d in affected_keys:
        aff_by_sym[sym].append(d)
    tdx_pages = sum(max(1, -(-((max(v) - min(v)).days + 1) // 800)) for v in amb_by_sym.values())
    tdx_pages_affected = sum(
        max(1, -(-((max(v) - min(v)).days + 1) // 800)) for v in aff_by_sym.values()
    )
    baostock_requests = len(amb_by_sym)
    baostock_requests_affected = len(aff_by_sym)

    return {
        "TDX_ROW_N": int(tdx.height),
        "PROVABLY_AFFECTED_N": counts["PROVABLY_AFFECTED"],
        "PROVABLY_UNAFFECTED_N": counts["PROVABLY_UNAFFECTED"],
        "AMBIGUOUS_N": counts["AMBIGUOUS"],
        "SUM_CHECK": {"sum": total, "equals_TDX_ROW_N": total == int(tdx.height)},
        "AFFECTED_SYMBOL_N": len(affected_symbols),
        "AMBIGUOUS_SYMBOL_N": len(ambiguous_symbols),
        "V01_REFERENCE": {
            "PROVABLY_AFFECTED_N": 201,
            "PROVABLY_UNAFFECTED_N": 10295597,
            "AMBIGUOUS_N": 29995,
        },
        "V01_1_DIFF": {
            "ROWS_CHANGED_CLASSIFICATION_N": sum(
                n for (a, b), n in transitions.items() if a != b
            ),
            "TRANSITIONS": {
                f"{a} -> {b}": n for (a, b), n in sorted(transitions.items())
            },
            "EFFECT_CORRECTED_128_FLOOR_N": floor128_affected_n,
            "EFFECT_VWAP_OVERRIDE_REMOVAL_N": vwap_override_unaffected_n,
        },
        "HARD_INTERSECTION": {
            "KNOWN_HARD_N": len(hard_keys),
            "HARD_AND_AFFECTED_N": hard_affected,
            "HARD_AND_AMBIGUOUS_N": hard_ambiguous,
            "HARD_AND_UNAFFECTED_N": hard_unaffected,
            "ALL_HARD_IN_AFFECTED_OR_AMBIGUOUS": hard_unaffected == 0,
        },
        "KNOWN_HARD_1110_COMPLETE_SET": "FALSE",
        "KNOWN_HARD_NOTE": (
            "1110 is a heuristic subset; V01.1 PROVABLY_AFFECTED contains "
            "non-HARD rows too, and AMBIGUOUS rows may be affected, so the "
            "HARD set cannot be a complete affected set.  Containment in "
            "AFFECTED u AMBIGUOUS is necessary but not sufficient."
        ),
        "AMOUNT_GATE": amount_global_correctness(),
        "SUPPORTING_DIAGNOSTIC": {
            "affected_rows_with_diagnostic_n": supporting["affected_n_with_diag"],
            "affected_corrected_vwap_in_range_n": supporting[
                "affected_corrected_vwap_in_range_n"
            ],
            "note": "amount/OHLC diagnostic only; never reclassifies",
        },
        "AFFECTED_KEYS": affected_plan,
        "AMBIGUOUS_KEYS": ambiguous_plan,
        "REBUILD_DECISION": (
            "OFFLINE_DETERMINISTIC_REBUILD_POSSIBLE"
            if counts["AMBIGUOUS"] == 0
            else "TARGETED_NETWORK_REFETCH_COMPLETE_SET"
        ),
        "TARGET_KEY_N": counts["AMBIGUOUS"],
        "TARGET_SYMBOL_N": len(ambiguous_symbols),
        "NETWORK_COST_PLAN": {
            "shape_A_TDX_CORRECTED_RUNTIME": {
                "requests_estimate": {"AMBIGUOUS_SYMBOLS": tdx_pages, "AFFECTED_SYMBOLS": tdx_pages_affected},
                "note": "paginated daily-K refetch per ambiguous symbol over its exact date span only (800 rows/page), targeted windows",
            },
            "shape_B_BAOSTOCK_SECONDARY": {
                "requests_estimate": {"AMBIGUOUS_SYMBOLS": baostock_requests, "AFFECTED_SYMBOLS": baostock_requests_affected},
                "note": "one bounded query per ambiguous symbol over its exact min..max date span; exact bounded windows queried safely, no full-symbol history assumed",
            },
            "NO_EXECUTION": True,
        },
        "300546_MISSING_DAYS": {
            "2016-09-29": "separate authority decision; NO insertion in this task",
            "2016-10-10": "separate authority decision; NO insertion in this task",
        },
        "SAFETY": {
            "NETWORK_PROVIDER_DATA_FETCH": "NO",
            "R3_MARKET_DATA_WRITE": "NO",
            "R3_DATA_REBUILD_EXECUTED": False,
            "R4A9_RESUME_AUTHORIZED": False,
            "PRECLOSE_COMPLETE": False,
        },
        "TARGET_KEY_ROWS": sorted(
            target_key_rows, key=lambda r: (r["symbol"], r["trade_date"])
        ),
    }


class InputDriftDuringScan(RuntimeError):
    """Raised when the canonical input changes while it is being scanned."""

    def __init__(self, pre: dict[str, Any], post: dict[str, Any]) -> None:
        super().__init__("INPUT_DRIFT_DURING_SCAN")
        self.pre = pre
        self.post = post


def input_manifests_equal(pre: dict[str, Any], post: dict[str, Any]) -> bool:
    """Compare the complete input binding required by the freeze contract."""
    return (
        pre["INPUT_FILE_N"] == post["INPUT_FILE_N"]
        and pre["INPUT_MANIFEST_HASH"] == post["INPUT_MANIFEST_HASH"]
        and pre["FILES"] == post["FILES"]
    )


def scan_canonical_with_input_manifest(
    data_root: Path,
    *,
    manifest_builder: Callable[[Path], dict[str, Any]] | None = None,
    scanner: Callable[[Path], dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Run the audited scan only when its complete input is provenance-bound.

    The pre-scan manifest is deliberately built immediately before the
    existing scanner, and the post-scan manifest is built immediately after
    it.  No output is written by this function; callers can therefore fail
    closed before accepting or persisting a target freeze.
    """
    build_manifest = manifest_builder or build_input_file_manifest
    scan = scanner or scan_canonical

    pre = build_manifest(data_root)
    result = scan(data_root)
    post = build_manifest(data_root)
    if not input_manifests_equal(pre, post):
        raise InputDriftDuringScan(pre, post)
    return result, pre, post


def run_target_freeze(
    data_root: Path,
    *,
    input_manifest_out: Path | None = None,
    target_manifest_out: Path | None = None,
    scanner: Callable[[Path], dict[str, Any]] | None = None,
    manifest_builder: Callable[[Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the scan and write outputs only after PRE == POST."""
    result, pre, post = scan_canonical_with_input_manifest(
        data_root,
        manifest_builder=manifest_builder,
        scanner=scanner,
    )

    manifest_section: dict[str, Any] = {
        "PRE_INPUT_FILE_N": pre["INPUT_FILE_N"],
        "PRE_INPUT_MANIFEST_HASH": pre["INPUT_MANIFEST_HASH"],
        "POST_INPUT_FILE_N": post["INPUT_FILE_N"],
        "POST_INPUT_MANIFEST_HASH": post["INPUT_MANIFEST_HASH"],
        "INPUT_STABLE_DURING_SCAN": True,
        "PRE_POST_FILES_EQUAL": True,
        "INPUT_MANIFEST_CANONICAL": pre["CANONICAL_SERIALIZATION"],
    }
    if input_manifest_out is not None:
        manifest_section["INPUT_MANIFEST_FILE"] = str(input_manifest_out)
        input_manifest_out.parent.mkdir(parents=True, exist_ok=True)
        input_manifest_out.write_text(
            json.dumps(pre, indent=1, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        print(f"written: {input_manifest_out}")
    if target_manifest_out is not None:
        manifest_section.update(
            write_target_manifest(result["TARGET_KEY_ROWS"], target_manifest_out)
        )
        print(f"written: {target_manifest_out}")
    result["MANIFEST"] = manifest_section
    result.pop("TARGET_KEY_ROWS", None)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--input-manifest-out", type=Path, default=None)
    parser.add_argument("--target-manifest-out", type=Path, default=None)
    args = parser.parse_args()
    try:
        result = run_target_freeze(
            args.data_root,
            input_manifest_out=args.input_manifest_out,
            target_manifest_out=args.target_manifest_out,
        )
    except InputDriftDuringScan as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if args.out is not None:
        args.out.write_text(
            json.dumps(result, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
