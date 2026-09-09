#!/usr/bin/env python3
"""Bootstrap the explicit, versioned R3 SH/SZ authority pointer once."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/luke808/AI/local-a-share-data-service-data")
SOURCE = ROOT / "staging/r3_full_session_completeness_authority_v01/full_request_manifest.json"
DIRECTORY = ROOT / "meta/asl/r3/shsz-authorities"
POINTER = ROOT / "meta/asl/r3/active-shsz-authority.json"
VERSION = "R3_SHSZ_AUTHORITY_V01"


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical(value) + b"\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def build() -> dict:
    source = json.loads(SOURCE.read_text())
    records: dict[str, dict] = {}
    for row in source.get("requests", []):
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol.endswith((".SH", ".SZ")):
            raise RuntimeError("SOURCE_AUTHORITY_SYMBOL_INVALID")
        lifecycle = {"symbol": symbol, "list_date": row.get("list_date"), "delist_date": row.get("delist_date")}
        if symbol in records and records[symbol] != lifecycle:
            raise RuntimeError("SOURCE_AUTHORITY_LIFECYCLE_CONFLICT")
        records[symbol] = lifecycle
    symbols = [records[symbol] for symbol in sorted(records)]
    names = [row["symbol"] for row in symbols]
    if source.get("FORMAL_SYMBOL_N") != len(symbols) or source.get("FORMAL_IDENTITY_HASH") != digest(names):
        raise RuntimeError("SOURCE_AUTHORITY_IDENTITY_MISMATCH")
    return {"schema": "ASL_R3_SHSZ_AUTHORITY_V01", "authority_version": VERSION,
            "effective_from": "2026-09-10", "symbol_n": len(symbols), "identity_hash": digest(names),
            "source": {"relative_path": SOURCE.relative_to(ROOT).as_posix(), "source_manifest_hash": digest(source),
                       "provider": "BAOSTOCK_STOCK_BASIC_FROZEN_R3"},
            "created_at": datetime.now(timezone.utc).isoformat(), "previous_authority_hash": None,
            "lifecycle": symbols,
            "quality": {"IDENTITY_PASS": True, "LIFECYCLE_PASS": True, "NO_SILENT_EXPANSION": True, "PASS": True}}


def activate(*, execute: bool) -> dict:
    artifact = DIRECTORY / f"{VERSION}.json"
    if POINTER.exists():
        existing = json.loads(POINTER.read_text())
        if (existing.get("schema") != "ASL_ACTIVE_R3_SHSZ_AUTHORITY_V01" or existing.get("authority_version") != VERSION
                or existing.get("authority") != artifact.relative_to(ROOT).as_posix()):
            raise RuntimeError("AUTHORITY_REFRESH_REQUIRED")
        if not artifact.exists() or digest(json.loads(artifact.read_text())) != existing.get("authority_hash"):
            raise RuntimeError("ACTIVE_AUTHORITY_DRIFT")
        current = build()
        active = json.loads(artifact.read_text())
        if (active.get("symbol_n"), active.get("identity_hash")) != (current["symbol_n"], current["identity_hash"]):
            raise RuntimeError("AUTHORITY_REFRESH_REQUIRED")
        return {"status": "ALREADY_ACTIVE", **existing}
    authority = build(); authority_hash = digest(authority)
    pointer = {"schema": "ASL_ACTIVE_R3_SHSZ_AUTHORITY_V01", "authority_version": VERSION,
               "authority": artifact.relative_to(ROOT).as_posix(), "authority_hash": authority_hash}
    if execute:
        atomic_json(artifact, authority)
        if digest(json.loads(artifact.read_text())) != authority_hash:
            raise RuntimeError("AUTHORITY_ARTIFACT_DRIFT")
        atomic_json(POINTER, pointer)
    return {"status": "ACTIVATED" if execute else "READY", **pointer}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--execute", action="store_true")
    print(json.dumps(activate(execute=parser.parse_args().execute), sort_keys=True))
