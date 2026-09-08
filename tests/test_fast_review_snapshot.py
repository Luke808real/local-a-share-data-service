from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fast_review_snapshot", ROOT / "src/ashare_data/fast_review_snapshot.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)
FastReviewError = module.FastReviewError
FastReviewSnapshotV01 = module.FastReviewSnapshotV01


def _row(symbol: str = "002580", market: int = 0) -> dict[str, object]:
    return {"f12": symbol, "f13": market, "f2": 21.05, "f3": 9.9791, "f5": 1,
            "f6": 1, "f7": 1, "f8": 1, "f15": 21.05, "f16": 19.05,
            "f17": 19.09, "f18": 19.14, "f20": 1, "f21": 1}


def test_acquire_delegates_complete_pagination_to_cnequity() -> None:
    with patch.object(module, "fetch_clist_pages", return_value=[_row()]):
        snapshot = FastReviewSnapshotV01.acquire(object())
    assert snapshot.rows[0]["symbol"] == "002580.SZ"


def test_partial_cnequity_pagination_fails_closed_without_cache(tmp_path: Path) -> None:
    with patch.object(module, "fetch_clist_pages", side_effect=RuntimeError("truncated")), pytest.raises(FastReviewError, match="SOURCE_ERROR"):
        FastReviewSnapshotV01.acquire(object())
    assert list(tmp_path.iterdir()) == []


def test_review_cache_requires_semantics_and_cannot_be_authority(tmp_path: Path) -> None:
    snapshot = FastReviewSnapshotV01(rows=({"symbol": "002580.SZ", "provider_row": _row()},), acquired_at="x")
    target = tmp_path / "review.json"
    with pytest.raises(FastReviewError, match="SEMANTICS_NOT_PASS"):
        snapshot.persist_review_cache(target, {"overall": "FAIL"})
    receipt = snapshot.validate_semantics(lambda _symbol: {"close": 21.05, "high": 21.05, "low": 19.05, "open": 19.09})
    snapshot.persist_review_cache(target, receipt)
    assert '"publication_authority":false' in target.read_text()


def test_price_field_semantic_mismatch_fails_closed() -> None:
    bad = _row(); bad["f3"] = 0
    snapshot = FastReviewSnapshotV01(rows=({"symbol": "002580.SZ", "provider_row": bad},), acquired_at="x")
    with pytest.raises(FastReviewError, match="PRICE_SEMANTIC_ERROR"):
        snapshot.validate_semantics(lambda _symbol: {"close": 21.05, "high": 21.05, "low": 19.05, "open": 19.09})
