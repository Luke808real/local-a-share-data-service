from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "AGENTS.md",
    "docs/MASTER_SPEC.md",
    "docs/ROADMAP.md",
    "docs/PROJECT_STATE.md",
    "docs/DECISIONS.md",
]


def test_required_project_authority_files_exist():
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).is_file()]
    assert missing == []


def test_master_spec_is_frozen_v1():
    text = (ROOT / "docs/MASTER_SPEC.md").read_text(encoding="utf-8")
    assert "SPEC_VERSION" in text
    assert "V1.0 FROZEN" in text
    assert "APPROVED / FROZEN" in text
    assert "CNEquity" in text
    assert "LOCAL_DATA_MVP" in text


def test_project_state_declares_r0_independent_audit_pending():
    text = (ROOT / "docs/PROJECT_STATE.md").read_text(encoding="utf-8")
    assert "CURRENT_PHASE: R0 — SPEC FREEZE" in text
    assert "LAST_AUDIT: R0 author verification complete; independent audit pending." in text
    assert "NEXT_ACTION: Independent audit of the exact pushed R0 review commit." in text
    assert "Do not execute R1 before AUDIT_PASS." in text


def test_agents_contract_contains_hard_boundaries():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for phrase in [
        "docs/MASTER_SPEC.md",
        "docs/PROJECT_STATE.md",
        "FULL_MARKET_AUTHORIZED = YES",
        "DESIGN_DECISION_REQUIRED",
        "B1/B2",
    ]:
        assert phrase in text
