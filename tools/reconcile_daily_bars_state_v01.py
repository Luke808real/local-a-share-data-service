#!/usr/bin/env python3
"""Controlled CNEquity StateStore migration for existing daily-bar coverage."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from cnequity.config import load_config
from cnequity.domain.datasets import get_dataset
from cnequity.quality.verify import last_contiguous_dense_date, verify_dataset
from cnequity.storage.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))
from ashare_data.local_query import LocalQuery  # noqa: E402

SCHEMA = "ASL_DAILY_BARS_STATE_MIGRATION_V01"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-state-migration-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def reconcile(config_path: Path, *, apply: bool) -> dict:
    config = load_config(config_path)
    spec = get_dataset("daily_bars")
    state = StateStore(config.meta_root)
    before = state.get_date("daily_bars")
    derived = last_contiguous_dense_date(config, spec)
    if derived is None:
        raise RuntimeError("STATE_RECONCILIATION_BLOCKED: no contiguous daily-bar coverage")
    gaps = [gap for gap in verify_dataset(config, spec, anchor=derived, watermark=derived) if gap.kind == "interior"]
    with LocalQuery(config.data_root) as query:
        status = query.status()
    published = status.get("DAILY_PUBLISHED_AS_OF")
    if published != derived.isoformat() or gaps:
        raise RuntimeError("STATE_RECONCILIATION_BLOCKED: derived coverage and R3 authority disagree")
    receipt = {
        "schema": SCHEMA,
        "cnequity_version": importlib.metadata.version("cnequity"),
        "data_root": str(config.data_root),
        "watermark_before": before.isoformat() if before else None,
        "derived_contiguous_daily_date": derived.isoformat(),
        "r3_published_as_of": published,
        "r3_manifest_hash": status.get("DAILY_MANIFEST_HASH"),
        "r3_quality_status": status.get("PUBLICATION_STATUS"),
        "interior_gap_n": len(gaps),
        "network_refetch_n": 0,
        "applied": apply,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if apply:
        state.update_max_date("daily_bars", derived)
        after = state.get_date("daily_bars")
        if after != derived:
            raise RuntimeError("STATE_RECONCILIATION_BLOCKED: StateStore did not persist derived date")
        receipt["watermark_after"] = after.isoformat()
        encoded = json.dumps(receipt, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        receipt["receipt_hash"] = hashlib.sha256(encoded.encode()).hexdigest()
        _atomic_json(config.meta_root / "asl/state_migrations/daily-bars-state-migration-v01.json", receipt)
    else:
        receipt["watermark_after"] = before.isoformat() if before else None
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config/cnequity.toml")
    parser.add_argument("--apply", action="store_true")
    print(json.dumps(reconcile(parser.parse_args().config, apply=parser.parse_args().apply), sort_keys=True))
