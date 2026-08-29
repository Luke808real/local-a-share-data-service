from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import rebase_r4a9_status_conflict_checkpoint_v01 as checkpoint  # noqa: E402


def test_checkpoint_contract_is_frozen_to_exact_overlay_and_prior_authority() -> None:
    assert checkpoint.BASE_HEAD == "c58f18475fd6284e6d812388ff042f24c3e6d6ef"
    assert checkpoint.UPSTREAM_CHECKPOINT_SHA256 == "b3cf88438c94a46d077e0d37df6b70cb45d29c741e646264a061dc0ea8d804de"
    assert checkpoint.OVERLAY_KEY_N == 4
    assert checkpoint.OVERLAY_KEYSET_HASH == "49fd7d316e2a09bbb18f0b840d4a5034f3efb2dbba57e9f60255c7a8910b2663"


def test_checkpoint_stage_root_rejects_curated_outside_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "data"
    (root / "staging").mkdir(parents=True)
    expected = root / "staging" / checkpoint.STAGE_NAME
    assert checkpoint.require_stage_root(root, expected) == expected
    with pytest.raises(checkpoint.CheckpointError, match="CHECKPOINT_STAGE_ROOT_MISMATCH"):
        checkpoint.require_stage_root(root, root / "curated" / "daily_bars")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        expected.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink unsupported")
    with pytest.raises(checkpoint.CheckpointError, match="CHECKPOINT_STAGE_ROOT_MISMATCH|CHECKPOINT_STAGE_SYMLINK"):
        checkpoint.require_stage_root(root, expected)
