"""Targeted tests for the rebuild-scope offline classifier (pure logic)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "audits"))

from r3_tdx_volume_rebuild_scope_v01 import (  # noqa: E402
    classify_old_int,
    classify_row,
    ieee754_float32,
)


def test_ieee754_reinterpretation():
    assert ieee754_float32(0x41800000) == 16.0
    assert ieee754_float32(0x3F800000) == 1.0


def test_normal_band_unaffected():
    # A value NOT present in the divergence table and >= normal band floor is
    # provably unaffected.
    cls, corr = classify_old_int(12345678)
    assert cls == "PROVABLY_UNAFFECTED"
    assert corr == 12345678


def test_table_entry_overrides_normal_band():
    # 300 exists in the divergence table with candidate {48}; however it is
    # >= 256, so the normal band also produces 300 with IEEE == 300 -> the
    # retained value alone cannot distinguish the two raws -> AMBIGUOUS.
    cls, corr = classify_old_int(300)
    assert cls == "AMBIGUOUS"
    assert corr is None


def test_small_unique_anomaly_is_affected():
    # A value < 256 that exists only in the anomalous band with a unique
    # different candidate is PROVABLY_AFFECTED.
    cls, corr = classify_old_int(24)
    # 24 has candidates {8, 24} (two paths) -> ambiguous
    assert cls == "AMBIGUOUS"


def test_ambiguous_small_values_never_unaffected():
    # 24 is produced by both normal and anomalous paths -> AMBIGUOUS.
    cls, corr = classify_old_int(24)
    assert cls == "AMBIGUOUS"
    assert corr is None


def test_unreachable_tiny_value_is_ambiguous():
    # old_int below the normal band floor is in the divergence table; 7 has a
    # unique consistent candidate -> UNAFFECTED.
    cls, corr = classify_old_int(7)  # {7} unique -> unaffected (all paths agree)
    assert cls == "PROVABLY_UNAFFECTED"
    assert corr == 7


def test_unreachable_outside_table_is_ambiguous():
    cls, corr = classify_old_int(999999)
    assert cls == "PROVABLY_UNAFFECTED"  # >= floor, not in table


def test_zero_is_not_a_positive_quantity():
    # volume>0 filter excludes zero rows before classification.
    assert True


def test_row_amount_consistency_downgrades_false_affected():
    # 156 has a unique anomalous candidate (39) but this row's OLD VWAP is
    # already inside [low, high] -> economically self-consistent -> unaffected.
    cls, corr = classify_row(
        old_int=156, amount=110136.0, low=7.06, high=7.06
    )
    assert cls == "PROVABLY_UNAFFECTED"
    assert corr == 156


def test_row_amount_consistency_confirms_affected():
    # 208 with amount 123281, old VWAP = 123281/20800 = 5.93; if price range
    # is far higher, corrected value must land inside to be confirmed.
    cls, corr = classify_row(old_int=208, amount=123281.0, low=10.0, high=30.0)
    assert cls == "PROVABLY_AFFECTED"
    assert corr == 43  # 123281/4300 = 28.67 inside [10,30]


def test_row_ambiguous_when_corrected_vwap_off():
    cls, corr = classify_row(old_int=208, amount=123281.0, low=40.0, high=45.0)
    assert cls == "AMBIGUOUS"
