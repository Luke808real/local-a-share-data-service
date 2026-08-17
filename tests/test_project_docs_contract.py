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


def test_project_state_hands_off_blocked_r1_for_independent_audit():
    text = (ROOT / "docs/PROJECT_STATE.md").read_text(encoding="utf-8")
    assert "AS_OF: 2026-08-18" in text
    assert "CURRENT_PHASE: R1 — LOCAL ASSET AUDIT" in text
    assert "R0 AUDIT_PASS — exact commit" in text
    assert "0a96271b1a62cf1e2ab4e6eae48b3905c3601414" in text
    assert "independently reviewed via GitHub by GPT-5.6 Sol on 2026-08-17." in text
    assert "BRANCH: research/r1-local-asset-audit" in text
    assert "STATUS: CONTRACT_AUDITED_WITH_BLOCKERS" in text
    assert "VERSION: v0.7.2" in text
    assert "SHA: a18ee0484dfb0801650175471724def3228b8a17" in text
    assert "LOCAL_PACKAGE: NOT_INSTALLED" in text
    assert "STATUS: NOT_INITIALIZED" in text
    assert "NOT_PUBLISHED" in text
    for phrase in [
        "daily: CROSSCHECK_ONLY",
        "adj: CROSSCHECK_ONLY",
        "trading_status: CROSSCHECK_ONLY — UPSTREAM_CONTRACT_CONFLICT",
        "turnover: CROSSCHECK_ONLY — NOT_PRESENT_AT_PIN",
        "5m: CROSSCHECK_ONLY",
        "industry: REJECT — ABSENT_PRIMARY_SW_INDUSTRY",
        "index: CROSSCHECK_ONLY",
        "READONLY_BREACH_INDETERMINATE",
        "AUTHOR_STATUS: BLOCKED",
        "independent audit of the exact R1 commit",
        "DO_NOT_ACTIVATE_R2",
    ]:
        assert phrase in text
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
