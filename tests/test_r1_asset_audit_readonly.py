from pathlib import Path
import ast


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/r1_asset_audit.py"

LEGACY_ROOTS = {
    "/Users/luke808/AI/asl-shared",
    "/Users/luke808/AI/asl-r8-5m-lake",
    "/Users/luke808/AI/V flash/data",
}


def test_audit_script_exists():
    assert SCRIPT.is_file()


def test_legacy_roots_are_declared_inputs():
    text = SCRIPT.read_text(encoding="utf-8")
    for root in LEGACY_ROOTS:
        assert root in text


def test_script_does_not_use_obvious_destructive_filesystem_calls():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    banned = {"unlink", "remove", "rmdir", "rename", "replace", "write_text", "write_bytes"}
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in banned:
            offenders.append(node.attr)
    assert offenders == []
