"""Targeted tests for the corrected raw wire decoder (variable-length trace).

Covers the Sol audit blocker: price diffs are variable length (UTF-8-like:
bit6 sign, bit7 continuation, 7 data bits per continuation byte), so fixed
20-byte records are invalid. Synthetic records exercise 1/2/3-byte prices
and mixed consecutive records to prove record N+1 stays aligned.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "audits"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from r3_tdx_raw_wire_decoder_v01 import (  # noqa: E402
    decode_pinned_daily_record,
    ieee754_float32,
    trace_daily_bars,
)


def _encode_price(value: int) -> bytes:
    """UTF-8-like signed varint: bit6 sign, bit7 continuation, 7 data bits."""
    sign = 0x40 if value < 0 else 0
    mag = abs(value)
    chunks = [mag & 0x3F]
    mag >>= 6
    while mag:
        chunks.append(mag & 0x7F)
        mag >>= 7
    out = bytearray()
    for i, ch in enumerate(chunks):
        byte = ch | (0x80 if i < len(chunks) - 1 else 0)
        if i == 0:
            byte |= sign
        out.append(byte)
    return bytes(out)


def _record(
    date_code: int,
    prices: list[int],
    vol_raw: int,
    amount_raw: int,
) -> bytes:
    return (
        struct.pack("<I", date_code)  # category=9: 4-byte YYYYMMDD
        + b"".join(_encode_price(p) for p in prices)
        + struct.pack("<I", vol_raw)
        + struct.pack("<I", amount_raw)
    )


def test_price_encoding_lengths():
    # 6 data bits in first byte; each continuation byte carries 7 bits.
    assert len(_encode_price(63)) == 1    # fits 6 bits
    assert len(_encode_price(64)) == 2    # needs 7 bits
    assert len(_encode_price(-64)) == 2   # magnitude 64, needs 7 bits
    assert len(_encode_price(8191)) == 2  # 13 bits = 6 + 7
    assert len(_encode_price(8192)) == 3  # 14 bits = 6 + 7 + 1
    assert len(_encode_price(-8192)) == 3


def test_single_record_1byte_prices_alignment():
    body = b"\x01\x00" + struct.pack("<I", 20160930) + b"".join(
        _encode_price(p) for p in (1, 2, 3, 4)
    ) + struct.pack("<I", 0x41800000) + struct.pack("<I", 0x475E8000)
    rec = decode_pinned_daily_record(body, 2)
    assert rec is not None
    assert rec["trade_date"] == "2016-09-30"
    assert rec["vol_raw_uint32"] == 0x41800000
    assert rec["amount_raw_uint32"] == 0x475E8000
    assert rec["record_start_pos"] == 2
    assert rec["volume_pos"] == 2 + 4 + 4  # date4 + 4x1-byte prices
    assert rec["record_end_pos"] == rec["volume_pos"] + 8


def test_mixed_length_records_alignment():
    # Two records: first with 1-byte prices, second with 3-byte prices.
    rec1 = _record(20160930, [1, 2, 3, 4], 0x41800000, 0x475E8000)
    rec2 = _record(
        20161011, [8191, -8192, 1000, -1000], 0x42300000, 0x48391C00
    )
    body = b"\x02\x00" + rec1 + rec2
    records = trace_daily_bars(body)
    assert len(records) == 2
    assert records[0]["trade_date"] == "2016-09-30"
    assert records[1]["trade_date"] == "2016-10-11"
    assert records[0]["vol_raw_uint32"] == 0x41800000
    assert records[1]["vol_raw_uint32"] == 0x42300000
    assert records[1]["amount_raw_uint32"] == 0x48391C00
    # record 2 start == record 1 end
    assert records[1]["record_start_pos"] == records[0]["record_end_pos"]


def test_three_records_consecutive_alignment():
    recs = [
        _record(20161013, [5, 6, 7, 8], 0x424C0000, 0x47CD6D80),
        _record(20161014, [9, 10, 11, 12], 0x42380000, 0x47B6F000),
        _record(20161017, [13, 14, 15, 16], 0x42300000, 0x47A11000),
    ]
    body = b"\x03\x00" + b"".join(recs)
    records = trace_daily_bars(body)
    assert len(records) == 3
    dates = [r["trade_date"] for r in records]
    assert dates == ["2016-10-13", "2016-10-14", "2016-10-17"]
    for prev, nxt in zip(records, records[1:]):
        assert nxt["record_start_pos"] == prev["record_end_pos"]


def test_ieee754_reinterpretation():
    assert ieee754_float32(0x41800000) == 16.0
    assert ieee754_float32(0x3F800000) == 1.0
    assert ieee754_float32(0x42300000) == 44.0
    assert ieee754_float32(0x4949D320) == 826674.0
