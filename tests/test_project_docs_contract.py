from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "AGENTS.md",
    "docs/MASTER_SPEC.md",
    "docs/ROADMAP.md",
    "docs/PROJECT_STATE.md",
    "docs/DECISIONS.md",
    "docs/plans/R2_CNEQUITY_BASELINE_IMPLEMENTATION_PLAN.md",
    ".python-version",
    "pyproject.toml",
    "uv.lock",
    "config/cnequity.toml",
    "docs/contracts/R2_CNEQUITY_BASELINE_CONTRACT.md",
    "tools/verify_r2_baseline.py",
    "reports/audits/R1_LOCAL_ASSET_AUDIT_INDEPENDENT_REPORT.md",
    "reports/audits/R2_CNEQUITY_BASELINE_AUTHOR_REPORT.md",
    "reports/audits/R2_CNEQUITY_BASELINE_INDEPENDENT_REPORT.md",
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


def test_project_state_activates_r3_after_r2_audit_pass():
    text = (ROOT / "docs/PROJECT_STATE.md").read_text(encoding="utf-8")
    assert "AS_OF: 2026-08-18" in text
    assert "CURRENT_PHASE: R3 — DAILY FOUNDATION" in text
    assert "R3_EXECUTION: NOT_STARTED" in text
    assert "R0 AUDIT_PASS — exact commit" in text
    assert "0a96271b1a62cf1e2ab4e6eae48b3905c3601414" in text
    assert "independently reviewed via GitHub by GPT-5.6 Sol on 2026-08-17." in text
    assert "BRANCH: main" in text
    assert "HEAD: SELF — commit containing this file" in text
    assert "WORKTREE: DIRTY (pre-existing local-only untracked files; tracked tree clean)" in text
    assert "STATUS: BASELINE_PINNED" in text
    assert "VERSION: v0.7.2" in text
    assert "SHA: a18ee0484dfb0801650175471724def3228b8a17" in text
    assert "LOCAL_PACKAGE: INSTALLED_EXACT_GIT_PIN" in text
    assert "PYTHON: CPython 3.12.13" in text
    assert "LOCK_SHA256: 5f233fa9434624391c06e56a4596edfd52c1ec596d66688753b78f424dd571ac" in text
    assert "STATUS: INITIALIZED_LAYOUT_ONLY_ZERO_DATA" in text
    assert "PATH: /Users/luke808/AI/local-a-share-data-service-data" in text
    assert "CONFIG_PATH: config/cnequity.toml" in text
    assert "CONFIG_SHA256: fac5abd136cb2ae00c07d7ca408eb1d47eed69c26c3547a0547ef9d214063fb5" in text
    assert "TREE_SNAPSHOT_SHA256: ddcf9dc509b6bfb0cea8bd27511360ba6d1b4151b4a745f3e0fcb230ecd43dd5" in text
    assert "NOT_PUBLISHED" in text
    for phrase in [
        "instruments: NOT_BUILT",
        "daily: NOT_BUILT",
        "adj: NOT_BUILT",
        "trading_status: NOT_BUILT — CONTRACT_FROZEN_R4_IMPLEMENTATION_REQUIRED",
        "turnover: NOT_BUILT — CONTRACT_FROZEN_R4_IMPLEMENTATION_REQUIRED",
        "5m: NOT_BUILT",
        "industry: NOT_BUILT",
        "index: NOT_BUILT",
        "READONLY_BREACH_INDETERMINATE",
        "OWNER_RECOVERY: AUTHORIZED_AND_COMPLETED",
        "R1 AUDIT_PASS — exact pushed commit",
        "09e9254042ad747983d40d794595135fb58e2d80",
        "R2_AUTHOR_STATUS: PASS — AUTHOR_ONLY",
        "R2_AUDIT_STATUS: AUDIT_PASS",
        "AUDITED_COMMIT: e354f59297cc2cf9722304f39a315712761d4b91",
        "reports/audits/R2_CNEQUITY_BASELINE_AUTHOR_REPORT.md",
        "reports/audits/R2_CNEQUITY_BASELINE_INDEPENDENT_REPORT.md",
        "R2 AUDIT_PASS — exact pushed commit",
        "on_demand.enabled=false is not an enforceable network guard",
        "Provider selection, coverage proof, and implementation remain R4 work",
        "No legacy migration input is authorized",
        "Prepare and independently audit the R3 DAILY FOUNDATION implementation plan",
        "Do not execute R4 before R3 AUDIT_PASS",
    ]:
        assert phrase in text
    assert "R3 AUDIT_PASS — exact pushed commit" not in text
    assert "R3_EXECUTION: COMPLETE" not in text


def test_r2_author_report_is_complete_and_not_an_independent_audit():
    text = (
        ROOT / "reports/audits/R2_CNEQUITY_BASELINE_AUTHOR_REPORT.md"
    ).read_text(encoding="utf-8")
    for section in [
        "AUTHOR_STATUS",
        "SPEC_VERSION",
        "BASE_HEAD",
        "HEAD_BEFORE_FINAL_COMMIT",
        "UPSTREAM_CNEQUITY",
        "PYTHON_RUNTIME",
        "LOCK_STATUS",
        "CONFIG_STATUS",
        "DATA_ROOT",
        "LAYOUT_STATUS",
        "ZERO_MARKET_DATA",
        "TRADING_STATUS_CONTRACT",
        "TURNOVER_CONTRACT",
        "TESTS",
        "BLOCKERS",
        "PROHIBITIONS_CONFIRMED",
        "REPORT_PATH",
    ]:
        assert f"## {section}" in text
    for phrase in [
        "AUTHOR_STATUS: PASS",
        "author-only",
        "not AUDIT_PASS",
        "a21614f825e07adb69cd0269ee786bdf0ad0f5c1",
        "0263ecb7b6c446b47bc082e86ca72379a8c3eca2",
        "a18ee0484dfb0801650175471724def3228b8a17",
        "Python 3.12.13",
        "5f233fa9434624391c06e56a4596edfd52c1ec596d66688753b78f424dd571ac",
        "fac5abd136cb2ae00c07d7ca408eb1d47eed69c26c3547a0547ef9d214063fb5",
        "/Users/luke808/AI/local-a-share-data-service-data",
        "18 entries",
        "ingestion_runs=0",
        "ingestion_batches=0",
        "43 views",
        "0 physical tables",
        "R8",
        "R4",
        "reports/audits/R2_CNEQUITY_BASELINE_AUTHOR_REPORT.md",
    ]:
        assert phrase in text


def test_r2_independent_audit_report_records_exact_commit_and_zero_data_gate():
    text = (
        ROOT / "reports/audits/R2_CNEQUITY_BASELINE_INDEPENDENT_REPORT.md"
    ).read_text(encoding="utf-8")
    for phrase in [
        "R2: AUDIT_PASS",
        "e354f59297cc2cf9722304f39a315712761d4b91",
        "a21614f825e07adb69cd0269ee786bdf0ad0f5c1",
        "26 passed",
        "ingestion_runs=0",
        "ingestion_batches=0",
        "43 DuckDB views",
        "0 physical tables",
        "R2_EXIT_GATE: SATISFIED",
        "R3_ACTIVATION: ALLOWED_ADMINISTRATIVE_ONLY",
        "No P0, P1, P2, or P3 defect",
    ]:
        assert phrase in text


def test_r1_independent_audit_report_records_exact_commit_and_recovery():
    text = (
        ROOT / "reports/audits/R1_LOCAL_ASSET_AUDIT_INDEPENDENT_REPORT.md"
    ).read_text(encoding="utf-8")
    for phrase in [
        "R1: AUDIT_PASS",
        "09e9254042ad747983d40d794595135fb58e2d80",
        "R1_EXIT_GATE: SATISFIED",
        "R2_ACTIVATION: ALLOWED_WITH_FAIL_CLOSED_CARRY_FORWARD",
        "READONLY_BREACH_INDETERMINATE",
        "manifest.db-wal",
        "manifest.db-shm",
        "OWNER_RECOVERY",
    ]:
        assert phrase in text


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
