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


def test_project_state_activates_r1_after_r0_audit_pass():
    text = (ROOT / "docs/PROJECT_STATE.md").read_text(encoding="utf-8")
    assert "CURRENT_PHASE: R1 — LOCAL ASSET AUDIT" in text
    assert "R0 AUDIT_PASS — exact commit" in text
    assert "0a96271b1a62cf1e2ab4e6eae48b3905c3601414" in text
    assert "independently reviewed via GitHub by GPT-5.6 Sol on 2026-08-17." in text
    assert "Prepare and execute the approved R1 LOCAL ASSET AUDIT plan" in text
    assert "in strict read-only mode." in text
    assert "STATUS: NOT_INITIALIZED" in text
    assert "STATUS: NOT_AUDITED" in text
    assert "NOT_PUBLISHED" in text
    assert "R1 AUDIT_PASS" not in text


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
