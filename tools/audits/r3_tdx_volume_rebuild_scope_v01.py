#!/usr/bin/env python3
"""R3 TDX volume rebuild-scope offline classifier (research-only, V01).

Classifies every existing canonical TDX security daily volume row into
PROVABLY_UNAFFECTED / PROVABLY_AFFECTED / AMBIGUOUS using ONLY the retained
decoded value (canonical = int(old_get_volume(raw))*100), with no raw wire
bytes and no network.

Decoder-divergence mathematics (established offline):
  OLD = vendored packed get_volume(raw); NEW = IEEE-754 float32(raw).
  - Normal band (quantities >= 2^8 = 256 lots, lp >= 0x43): int(OLD) ==
    int(NEW) for every raw. The normal band is dense in integers >= 256, so
    every integer value >= 256 has a normal-band pre-image with IEEE ==
    old_int.
  - Divergence band 0x3c..0x42: a full 24-bit mantissa pre-image table maps
    old_int -> set of int(NEW) values for raws in this band only.

  Classification of canonical old_int (canonical = old_int * 100):
    old_int >= 256 and absent from divergence table
      -> PROVABLY_UNAFFECTED (normal band only; IEEE == old_int)
    present in divergence table with >1 candidate
      -> AMBIGUOUS (both a normal-band and an anomalous-band raw exist)
    present with unique candidate == old_int
      -> PROVABLY_UNAFFECTED (all paths agree)
    present with unique candidate != old_int and old_int >= 256
      -> AMBIGUOUS (normal band also produces old_int with IEEE == old_int,
         so the retained value alone cannot distinguish the two raws)
    present with unique candidate != old_int and old_int < 256
      -> PROVABLY_AFFECTED (the only producing band is anomalous; corrected
         value is the unique candidate)
    absent and old_int < 256
      -> AMBIGUOUS (unproven; never demoted to unaffected)

UNKNOWN/AMBIGUOUS is never treated as unaffected.
"""

from __future__ import annotations

import argparse
import json
import struct
from collections import defaultdict
from pathlib import Path
from typing import Any

import polars as pl
from cnequity.adapters.tdx_protocol._wire.helper import get_volume

DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")


def ieee754_float32(raw: int) -> float:
    return struct.unpack("<f", struct.pack("<I", raw & 0xFFFFFFFF))[0]


# Divergence bands (established offline): full mantissa enumeration of these
# bands is tractable; higher bands are provably unaffected.
DIVERGENCE_BANDS = range(0x3C, 0x43)
NORMAL_BAND_MIN_LP = 0x43
MAX_REAL_LP = 0x5A  # safety ceiling for the normal-band preimage scan
NORMAL_BAND_FLOOR = 256  # 2**8: smallest quantity in the normal band


def _build_preimage_table() -> dict[int, set[int]]:
    """old_int -> set(int(ieee754(raw))) over full mantissa, divergence bands."""
    table: dict[int, set[int]] = defaultdict(set)
    for lp in DIVERGENCE_BANDS:
        for m in range(0x1000000):
            raw = (lp << 24) | m
            o = int(get_volume(raw))
            n = int(ieee754_float32(raw))
            if o <= 0:
                continue
            table[o].add(n)
    return dict(table)


_TABLE: dict[int, set[int]] | None = None


def _table() -> dict[int, set[int]]:
    global _TABLE
    if _TABLE is None:
        _TABLE = _build_preimage_table()
    return _TABLE


def classify_old_int(old_int: int) -> tuple[str, int | None]:
    """Pure classification of one canonical//100 value."""
    table = _table()
    if old_int in table:
        cands = table[old_int]
        if len(cands) == 1:
            (val,) = cands
            if val == old_int:
                return "PROVABLY_UNAFFECTED", val
            if old_int < NORMAL_BAND_FLOOR:
                return "PROVABLY_AFFECTED", val
            return "AMBIGUOUS", None
        return "AMBIGUOUS", None
    if old_int >= NORMAL_BAND_FLOOR:
        return "PROVABLY_UNAFFECTED", old_int
    return "AMBIGUOUS", None


def classify_row(
    *,
    old_int: int,
    amount: float,
    low: float,
    high: float,
) -> tuple[str, int | None]:
    """Row-level classification with amount VWAP cross-validation.

    A PROVABLY_AFFECTED candidate is accepted only when the OLD canonical VWAP
    (amount / (old_int*100)) is OUTSIDE [low, high] AND the corrected VWAP
    (amount / (corrected*100)) falls INSIDE [low, high]. If the old VWAP is
    already inside the price range, the economic evidence contradicts the
    anomalous-path hypothesis (the old value is self-consistent) so the row is
    treated as unaffected regardless of the decoder table.
    """
    old_vwap = amount / (old_int * 100)
    old_plausible = low <= old_vwap <= high
    cls, corr = classify_old_int(old_int)
    if cls == "PROVABLY_AFFECTED":
        if old_plausible:
            return "PROVABLY_UNAFFECTED", old_int
        corr_vwap = amount / (corr * 100)
        if low <= corr_vwap <= high:
            return "PROVABLY_AFFECTED", corr
        return "AMBIGUOUS", None
    if cls == "PROVABLY_UNAFFECTED":
        return cls, old_int
    return cls, None


def _normal_band_has_value(old_int: int) -> bool:
    """Can some lp>=0x43 raw produce int(old)==old_int with int(ieee)==old_int?"""
    # All lp>=0x43 raws have int(old)==int(ieee) (verified offline, coarse+full).
    # Simply ask whether the value is in the achievable range of the band.
    lo = 256  # lp=0x43 with hleax=0 -> 2**8
    hi = 2**60  # far above any real daily giant; capped envelope
    return lo <= old_int <= hi


def scan_canonical(data_root: Path) -> dict[str, Any]:
    df = pl.read_parquet(str(data_root / "curated/daily_bars/**/*.parquet"))
    tdx = df.filter(
        (pl.col("source") == "tdx_protocol")
        & (pl.col("volume") > 0)
        & pl.col("volume").is_finite()
    )
    affected: dict[str, Any] = {
        "n": 0,
        "symbols": set(),
        "by_year": defaultdict(int),
        "by_exchange": defaultdict(int),
        "by_days_from_listing": defaultdict(int),
    }
    unaffected_n = 0
    ambiguous_n = 0
    corrected_map: dict[int, int] = {}
    _table()
    instr = pl.read_parquet(data_root / "curated/instruments/part-merged.parquet")
    list_map = {
        str(r["symbol"]): r["list_date"]
        for r in instr.select(["symbol", "list_date"]).iter_rows(named=True)
    }
    for row in tdx.iter_rows(named=True):
        vol = float(row["volume"])
        old_int = int(round(vol / 100))
        cls, corrected = classify_row(
            old_int=old_int,
            amount=float(row["amount"]),
            low=float(row["low"]),
            high=float(row["high"]),
        )
        if cls == "PROVABLY_AFFECTED":
            affected["n"] += 1
            affected["symbols"].add(row["symbol"])
            affected["by_year"][row["trade_date"].year] += 1
            ex = "SH" if row["symbol"].endswith(".SH") else "SZ"
            affected["by_exchange"][ex] += 1
            ld = list_map.get(row["symbol"])
            if ld is not None:
                days = (row["trade_date"] - ld).days
                b = (
                    "0-5"
                    if days <= 5
                    else "6-20"
                    if days <= 20
                    else "21-60"
                    if days <= 60
                    else "61-250"
                    if days <= 250
                    else ">250"
                )
                affected["by_days_from_listing"][b] += 1
            corrected_map[old_int] = corrected
        elif cls == "PROVABLY_UNAFFECTED":
            unaffected_n += 1
        else:
            ambiguous_n += 1
    return {
        "TDX_ROW_N": int(tdx.height),
        "PROVABLY_AFFECTED_N": affected["n"],
        "PROVABLY_UNAFFECTED_N": unaffected_n,
        "AMBIGUOUS_N": ambiguous_n,
        "AFFECTED_SYMBOL_N": len(affected["symbols"]),
        "by_year": dict(sorted(affected["by_year"].items())),
        "by_exchange": dict(affected["by_exchange"]),
        "by_days_from_listing": dict(affected["by_days_from_listing"]),
        "affected_old_int_corrections_sample": {
            str(k): v for k, v in list(corrected_map.items())[:20]
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    result = scan_canonical(args.data_root)
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
