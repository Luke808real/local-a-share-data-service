"""Targeted tests for the corrected rebuild-scope offline classifier.

All tests use pure functions with explicit candidate sets or direct
decoder calls; none builds the full divergence-band preimage table.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "audits"))

from cnequity.adapters.tdx_protocol._wire.helper import get_volume  # noqa: E402
from r3_tdx_volume_rebuild_scope_v01 import (  # noqa: E402
    NORMAL_BAND_FLOOR,
    VALID_MIN_QUANTITY,
    amount_global_correctness,
    amount_supporting_diagnostic,
    classify_old_int_v01_with,
    classify_row_v01_with,
    divide_into_candidates,
    ieee754_float32,
    is_valid_quantity_raw,
    normal_band_raw_candidate,
    normal_candidate_exists,
    valid_raw_domain_bounds,
)


def test_ieee754_reinterpretation():
    assert ieee754_float32(0x41800000) == 16.0
    assert ieee754_float32(0x3F800000) == 1.0


# ---------------------------------------------------------------------------
# 1. VALID RAW DOMAIN
# ---------------------------------------------------------------------------


def test_valid_domain_minimum_is_float32_of_one_share():
    # minimum positive volume = 1 share = 0.01 lots; the smallest stored
    # float32 that can encode it is RN(0.01) = 0x3C23D70A.
    assert VALID_MIN_QUANTITY == struct.unpack("<f", struct.pack("<f", 0.01))[0]
    assert struct.unpack("<I", struct.pack("<f", 0.01))[0] == 0x3C23D70A
    assert is_valid_quantity_raw(0x3C23D70A)


def test_lp_below_3c_excluded():
    # lp < 0x3C decodes below 2**-7 lots = 0.78125 shares < 1 share.
    assert ieee754_float32(0x3BFFFFFF) < 0.0078125
    assert ieee754_float32(0x3BFFFFFF) < 0.01
    assert not is_valid_quantity_raw(0x3BFFFFFF)
    assert not is_valid_quantity_raw(0x3B000000)


def test_lp_3c_sub_share_portion_excluded():
    # 0x3C000000 = 2**-7 lots (0.78125 shares) is below 1 share.
    assert ieee754_float32(0x3C000000) == 0.0078125
    assert not is_valid_quantity_raw(0x3C000000)


def test_domain_endpoints_positive():
    assert is_valid_quantity_raw(0x3C23D70B)  # just above RN(0.01)
    assert is_valid_quantity_raw(0x3D800000)  # 0.0625 lots
    assert is_valid_quantity_raw(0x3F800000)  # 1.0 lots
    assert is_valid_quantity_raw(0x43000000)  # 128.0 lots


def test_domain_proof_bundle():
    bounds = valid_raw_domain_bounds()
    assert bounds["MIN_POSITIVE_NATIVE_LOTS"] == 0.01
    assert bounds["VALID_MIN_RAW_HEX"] == "0x3C23D70A"
    assert bounds["LP_RANGE"][0] == "0x3C"
    assert "0.78125" in bounds["LP_BELOW_3C_EXCLUDED"]
    assert bounds["NORMAL_BAND_FLOOR"] == 128


# ---------------------------------------------------------------------------
# 2. NORMAL BAND FLOOR 128 - EXPLICIT PREIMAGES
# ---------------------------------------------------------------------------


def test_normal_band_raw_candidates_explicit():
    # Sol audit examples: lp=0x43 includes 128..256 integer preimages.
    assert normal_band_raw_candidate(128) == 0x43000000
    assert normal_band_raw_candidate(156) == 0x431C0000
    assert normal_band_raw_candidate(208) == 0x43500000
    assert normal_band_raw_candidate(255) == 0x437F0000
    assert normal_band_raw_candidate(256) == 0x43800000


def test_normal_band_candidates_decode_to_old_int():
    for k in (128, 156, 208, 255, 256, 300, 1000, 65536):
        raw = normal_band_raw_candidate(k)
        assert raw is not None
        # normal band: old decodes exactly like IEEE and int-truncates to k
        assert int(get_volume(raw)) == k
        assert int(ieee754_float32(raw)) == k


def test_known_anomaly_raw_pairs():
    # pinned old decoder vs IEEE on established anomaly raws
    assert int(get_volume(0x41800000)) == 2056
    assert int(ieee754_float32(0x41800000)) == 16
    assert int(get_volume(0x42300000)) == 224
    assert int(ieee754_float32(0x42300000)) == 44
    assert int(get_volume(0x3F800000)) == 32768
    assert int(ieee754_float32(0x3F800000)) == 1


def test_normal_candidate_existence_boundaries():
    assert not normal_candidate_exists(0)
    assert not normal_candidate_exists(127)
    assert normal_candidate_exists(128)
    assert normal_candidate_exists(255)
    assert normal_candidate_exists(1 << 24)
    # 2**24 + 1 is not representable: no float32 v with int(v) == k
    assert not normal_candidate_exists((1 << 24) + 1)
    # 2**25 + 4 is representable (trailing zeros >= 2)
    assert normal_candidate_exists((1 << 25) + 4)
    assert normal_candidate_exists(60_110_592)


# ---------------------------------------------------------------------------
# 3. CANDIDATE-SET CLASSIFIER (PURE, NO PLAUSIBILITY OVERRIDE)
# ---------------------------------------------------------------------------


def test_classify_normal_band_only():
    # k >= 128 with no anomaly candidates and a normal preimage -> UNAFFECTED
    assert divide_into_candidates(156, set()) == ("PROVABLY_UNAFFECTED", 156)
    assert divide_into_candidates(12345678, set()) == ("PROVABLY_UNAFFECTED", 12345678)


def test_classify_floor128_ambiguous_when_anomaly_candidate_differs():
    # 128..255 with a divergent anomaly candidate are AMBIGUOUS (normal
    # preimage also exists) - V01 floor 256 wrongly called these AFFECTED.
    assert divide_into_candidates(128, {16}) == ("AMBIGUOUS", None)
    assert divide_into_candidates(156, {39}) == ("AMBIGUOUS", None)
    assert divide_into_candidates(208, {43}) == ("AMBIGUOUS", None)
    assert divide_into_candidates(255, {64}) == ("AMBIGUOUS", None)
    assert divide_into_candidates(256, {16}) == ("AMBIGUOUS", None)


def test_classify_floor128_self_consistent_candidates():
    # anomaly candidate equal to retained value + normal preimage -> UNAFFECTED
    assert divide_into_candidates(156, {156}) == ("PROVABLY_UNAFFECTED", 156)
    assert divide_into_candidates(128, {128}) == ("PROVABLY_UNAFFECTED", 128)


def test_classify_sub_floor_unique_affected():
    # k < 128: no normal preimage; unique anomaly candidate != k -> AFFECTED
    assert divide_into_candidates(44, {11}) == ("PROVABLY_AFFECTED", 11)
    assert divide_into_candidates(7, {7}) == ("PROVABLY_UNAFFECTED", 7)


def test_classify_ambiguous_small_values_never_unaffected():
    assert divide_into_candidates(24, {8, 24}) == ("AMBIGUOUS", None)


def test_classify_model_gap_fails_closed():
    # no candidate at all: retained row unexplained -> fail closed AMBIGUOUS
    assert divide_into_candidates(44, set())[0] == "AMBIGUOUS"
    assert divide_into_candidates((1 << 24) + 1, set())[0] == "AMBIGUOUS"


def test_vwap_never_overrides_classification():
    # economic plausibility is a diagnostic, not a class change
    assert divide_into_candidates(156, {39}) == ("AMBIGUOUS", None)
    assert divide_into_candidates(44, {11}) == ("PROVABLY_AFFECTED", 11)
    diag = amount_supporting_diagnostic(
        old_int=156, corrected=None, amount=110136.0, low=7.06, high=7.06
    )
    assert diag["RETAINED_VWAP_IN_RANGE"] is True
    # even with a fully plausible retained VWAP the class stays AMBIGUOUS
    assert divide_into_candidates(156, {39}) == ("AMBIGUOUS", None)


# ---------------------------------------------------------------------------
# 4. AMOUNT CORRECTNESS GATE
# ---------------------------------------------------------------------------


def test_amount_global_correctness_unproven():
    gate = amount_global_correctness()
    assert gate["TDX_AMOUNT_GLOBAL_CORRECTNESS"] == "UNPROVEN"
    assert "SUPPORTING_DIAGNOSTIC" in gate["note"]


# ---------------------------------------------------------------------------
# 7. V01 vs V01.1 DIFF (both effects attributable)
# ---------------------------------------------------------------------------


def test_v01_floor_256_effect_affected_to_ambiguous():
    # V01 math: k=156 < 256 with unique anomaly candidate 39 -> AFFECTED.
    # V01.1: normal preimage exists (156 >= 128) -> AMBIGUOUS.
    v01_math = classify_old_int_v01_with(156, {39})
    assert v01_math == ("PROVABLY_AFFECTED", 39)
    assert divide_into_candidates(156, {39}) == ("AMBIGUOUS", None)

    # row-level V01 with VWAP confirmation is also AFFECTED; V01.1 same row
    # stays AMBIGUOUS regardless of amount.
    v01_row = classify_row_v01_with(
        old_int=156, amount=78000.0, low=10.0, high=30.0, anomaly_cands={39}
    )
    assert v01_row == ("PROVABLY_AFFECTED", 39)
    assert divide_into_candidates(156, {39}) == ("AMBIGUOUS", None)


def test_v01_vwap_override_effect_unaffected_to_ambiguous():
    # V01 row override demoted a mathematically AFFECTED row to UNAFFECTED
    # because the retained VWAP was self-consistent.  V01.1 keeps the math.
    v01_row = classify_row_v01_with(
        old_int=156, amount=110136.0, low=7.06, high=7.06, anomaly_cands={39}
    )
    assert v01_row == ("PROVABLY_UNAFFECTED", 156)
    assert divide_into_candidates(156, {39}) == ("AMBIGUOUS", None)


def test_v01_unaffected_case_unchanged():
    # fully self-consistent small value stays UNAFFECTED in both versions
    v01_row = classify_row_v01_with(
        old_int=7, amount=1000.0, low=10.0, high=10.0, anomaly_cands={7}
    )
    assert v01_row == ("PROVABLY_UNAFFECTED", 7)
    assert divide_into_candidates(7, {7}) == ("PROVABLY_UNAFFECTED", 7)
