"""Targeted tests for the raw wire decoder diagnostic tool (pure logic)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "audits"))

from r3_tdx_raw_wire_decoder_v01 import (  # noqa: E402
    independent_get_volume,
    parse_raw_bars_body,
)


def test_ieee754_canonical_values():
    # TDX daily volume is IEEE-754 float32 (independent authority evidence).
    assert independent_get_volume(0x41800000) == 16.0   # 16 lots = 1600 shares
    assert independent_get_volume(0x42300000) == 44.0   # 44 lots = 4400 shares
    assert independent_get_volume(0x3F800000) == 1.0    # 1 lot = 100 shares
    assert independent_get_volume(0x4949D320) == 826674.0
    assert independent_get_volume(0) == 0.0


def test_small_known_volume():
    # vol_raw = 2056 (0x808) -> logpoint=0, hleax=0, lheax=8, lleax=0x08
    v = independent_get_volume(2056)
    assert v > 0


def test_independent_and_pinned_agree_on_modern_healthy_rows():
    import cnequity.adapters.tdx_protocol._wire.helper as h  # pinned

    # Modern rows: both decoders agree (float32 and packed path coincide in
    # the range where the packed decoder is correct).
    for raw in (0x4949D320, 0x4EFDDD87):
        pinned = h.get_volume(raw)
        indep = independent_get_volume(raw)
        assert abs(pinned - indep) <= 1e-6 * max(1.0, abs(pinned)), (
            f"raw={hex(raw)} pinned={pinned} indep={indep}"
        )


def test_independent_and_pinned_diverge_on_anomaly_rows():
    import cnequity.adapters.tdx_protocol._wire.helper as h  # pinned

    # The anomaly rows: pinned packed decoder outputs the inflated values that
    # entered canonical (2056*100=205600 etc); independent IEEE-754 gives the
    # vendor-consistent lots.
    cases = {
        0x41800000: (2056.0, 16.0),    # 2016-09-30 300546.SZ
        0x3F800000: (32768.5, 1.0),    # 2016-09-29 / 2016-10-10
        0x42300000: (224.0, 44.0),     # 2016-10-11
    }
    for raw, (pinned_expected, indep_expected) in cases.items():
        assert abs(h.get_volume(raw) - pinned_expected) <= 1e-6, (hex(raw), h.get_volume(raw))
        assert abs(independent_get_volume(raw) - indep_expected) <= 1e-6


def test_parse_raw_bars_body_record_offsets():
    # Build a 2-byte count + one 20-byte record manually (4 price diffs empty).
    body = (
        b"\x01\x00"
        + b"\x00" * 12  # date2 time2 open2 close2 high2 low2
        + struct_pack_u32(2056)
        + struct_pack_u32(56960)
    )
    rows = parse_raw_bars_body(body)
    assert len(rows) == 1
    assert rows[0]["vol_raw_uint32"] == 2056
    assert rows[0]["amount_raw_uint32"] == 56960
    assert rows[0]["vol_raw_hex"] == "0x00000808"


def struct_pack_u32(value: int) -> bytes:
    import struct

    return struct.pack("<I", value)


def test_parse_raw_truncated_body_safe():
    rows = parse_raw_bars_body(b"\x05\x00" + b"\x00" * 8)
    assert rows == []
