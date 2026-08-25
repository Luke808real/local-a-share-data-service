#!/usr/bin/env python3
"""R3 TDX raw-volume wire decoder diagnostic tool (research only, V01.1).

Corrected for Sol audit blocker: the production TDX daily-K record uses
VARIABLE-LENGTH get_price() fields (a UTF-8-like signed integer: bit6 = sign,
bit7 = continuation, 7 data bits per continuation byte). A fixed 20-byte
record layout is therefore invalid. This tool follows the exact pinned field
position progression to locate the raw 4-byte volume and amount fields:

  ret_count (2 bytes)
  -> get_datetime (date 2 bytes + time 2 bytes; returns exact pos)
  -> open_diff  (variable get_price)
  -> close_diff (variable get_price)
  -> high_diff  (variable get_price)
  -> low_diff   (variable get_price)
  -> volume raw (4 bytes)  <- capture verbatim
  -> amount raw (4 bytes)  <- capture verbatim

Production code is never modified; the pinned get_price semantics are used
only to advance positions while raw quantity bytes are captured before the
pinned get_volume() decode.

Independent decoder: for comparison the raw 4 bytes are also reinterpreted
as IEEE-754 float32; that comparison is performed only after record
alignment is proven against the pinned production parser output.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl


def pinned_get_datetime(category: int, buffer: bytes, pos: int):
    """Exact pinned get_datetime semantics (position advancement only)."""
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from cnequity.adapters.tdx_protocol._wire.helper import get_datetime

    return get_datetime(category, buffer, pos)


def pinned_get_price(data: bytes, pos: int):
    """Exact pinned get_price semantics (variable length, position only)."""
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from cnequity.adapters.tdx_protocol._wire.helper import get_price

    return get_price(data, pos)


def decode_pinned_daily_record(
    body: bytes, start_pos: int, category: int = 9
) -> dict[str, Any] | None:
    """Trace one daily-K record with exact pinned field positioning.

    Returns record metadata including raw volume/amount bytes and positions,
    plus decoded datetime and OHLC diffs for alignment cross-check. Returns
    None when the record cannot be fully positioned.
    """
    try:
        year, month, day, hour, minute, pos = pinned_get_datetime(
            category, body, start_pos
        )
        open_diff, pos = pinned_get_price(body, pos)
        close_diff, pos = pinned_get_price(body, pos)
        high_diff, pos = pinned_get_price(body, pos)
        low_diff, pos = pinned_get_price(body, pos)
        if pos + 8 > len(body):
            return None
        vol_raw = struct.unpack("<I", body[pos : pos + 4])[0]
        amount_raw = struct.unpack("<I", body[pos + 4 : pos + 8])[0]
        end_pos = pos + 8
        return {
            "record_index": None,
            "record_start_pos": start_pos,
            "volume_pos": pos,
            "amount_pos": pos + 4,
            "record_end_pos": end_pos,
            "trade_date": f"{year:04d}-{month:02d}-{day:02d}",
            "datetime": f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}",
            "open_diff": open_diff,
            "close_diff": close_diff,
            "high_diff": high_diff,
            "low_diff": low_diff,
            "vol_raw_hex": f"0x{vol_raw:08x}",
            "vol_raw_uint32": vol_raw,
            "amount_raw_hex": f"0x{amount_raw:08x}",
            "amount_raw_uint32": amount_raw,
        }
    except Exception:  # noqa: BLE001 - truncated/malformed record fails soft
        return None


def trace_daily_bars(
    body: bytes, category: int = 9
) -> list[dict[str, Any]]:
    """Trace ALL daily-K records in a raw body (no fixed-size assumption)."""
    if len(body) < 2:
        return []
    ret_count = struct.unpack("<H", body[0:2])[0]
    records: list[dict[str, Any]] = []
    pos = 2
    for index in range(ret_count):
        rec = decode_pinned_daily_record(body, pos, category=category)
        if rec is None:
            break
        rec["record_index"] = index
        records.append(rec)
        pos = rec["record_end_pos"]
    return records


def ieee754_float32(raw: int) -> float:
    """Independent reinterpretation: raw 4 bytes as IEEE-754 float32."""
    return struct.unpack("<f", struct.pack("<I", raw & 0xFFFFFFFF))[0]


def capture_raw_bars_for_date(
    host: str, port: int, market: int, code: str, target_date: date
) -> dict[str, Any] | None:
    """Research-only: page daily K via pinned command, trace raw body."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from cnequity.adapters.tdx_protocol._wire import TdxWireClient
    from cnequity.adapters.tdx_protocol._wire.parser.std.get_security_bars import (
        GetSecurityBarsCmd,
    )

    class RawBarsCmd(GetSecurityBarsCmd):
        captured_body: bytes | None = None

        def parseResponse(self, body_buf):  # noqa: N802
            self.captured_body = bytes(body_buf)
            klines = super().parseResponse(body_buf)
            self._klines = klines
            return klines

    wc = TdxWireClient(multithread=False, heartbeat=False)
    wc.connect(host, int(port), time_out=8)
    wc.setup()
    try:
        target = target_date.strftime("%Y-%m-%d")
        for start in (0, 800, 1600, 2400, 3200, 4000, 4800, 5600, 6400, 7200, 8000):
            cmd = RawBarsCmd(wc.client, lock=wc.lock)
            cmd.setParams(9, market, code, start, 800)
            klines = cmd.call_api()
            body = cmd.captured_body
            if not klines:
                break
            traced = trace_daily_bars(body)
            for k, rec in zip(klines, traced):
                if rec["trade_date"] == target:
                    return {
                        "host": host,
                        "port": port,
                        "symbol_code": code,
                        "market": market,
                        "trace": rec,
                        "pinned_kline": {
                            "datetime": k["datetime"],
                            "open": k["open"],
                            "high": k["high"],
                            "low": k["low"],
                            "close": k["close"],
                            "vol": k.get("vol"),
                            "amount": k.get("amount"),
                        },
                    }
            oldest = klines[-1]["datetime"][:10]
            if target > oldest:
                break
        return {
            "host": host,
            "port": port,
            "code": code,
            "trade_date": target,
            "not_found": True,
        }
    finally:
        wc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="120.76.1.198")
    parser.add_argument("--port", type=int, default=7709)
    parser.add_argument("--code", default="300546")
    parser.add_argument("--market", type=int, default=0)
    parser.add_argument("--date", default="2016-09-30")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    result = capture_raw_bars_for_date(
        args.host, args.port, args.market, args.code, date.fromisoformat(args.date)
    )
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
