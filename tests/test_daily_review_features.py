from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("daily_review_features", ROOT / "src/ashare_data/daily_review_features.py")
module = importlib.util.module_from_spec(spec); assert spec.loader is not None
spec.loader.exec_module(module)


def test_features_are_local_non_authoritative_and_candidate_rule_is_explicit():
    rows = [{"trade_date": f"2026-08-{day:02d}", "open": 10., "high": 11., "low": 9., "close": 10 + day / 10,
             "volume": 100 + day, "amount": 1000 + day} for day in range(1, 22)]
    features = module.derive_features({"000001.SZ": rows})
    assert features[0]["publication_authority"] is False
    assert features[0]["scope"] == "REVIEW_EVIDENCE_ONLY_NOT_PUBLICATION"
    assert features[0]["ma20"] > 0
    assert isinstance(module.generic_candidates(features), list)
