import importlib
import importlib.metadata as metadata
import json
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_REPOSITORY = "https://github.com/rootSunc/CNEquity.git"
UPSTREAM_SHA = "a18ee0484dfb0801650175471724def3228b8a17"
CNEQUITY_VERSION = "0.7.2"
PINNED_REQUIREMENT = (
    "cnequity @ git+https://github.com/rootSunc/CNEquity.git@"
    f"{UPSTREAM_SHA}"
)
FORBIDDEN_DIRECT_DEPENDENCY_TERMS = ("strategy", "mcp", "scheduler")


def _read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_python_version_selects_cpython_312_line():
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"


def test_project_metadata_pins_only_the_frozen_cnequity_runtime():
    project = _read_toml(ROOT / "pyproject.toml")

    assert project["project"]["requires-python"] == ">=3.12,<3.13"
    assert project["project"]["dependencies"] == [PINNED_REQUIREMENT]
    assert project["dependency-groups"]["dev"] == ["pytest==8.4.2"]

    direct_dependencies = [
        *project["project"]["dependencies"],
        *project["dependency-groups"]["dev"],
    ]
    direct_dependency_text = "\n".join(direct_dependencies).lower()
    assert not any(
        term in direct_dependency_text for term in FORBIDDEN_DIRECT_DEPENDENCY_TERMS
    )


def test_lock_resolves_frozen_cnequity_git_commit():
    lock = _read_toml(ROOT / "uv.lock")
    cnequity_packages = [
        package
        for package in lock["package"]
        if package["name"].lower() == "cnequity"
    ]

    assert len(cnequity_packages) == 1
    package = cnequity_packages[0]
    assert package["version"] == CNEQUITY_VERSION

    source = package["source"]
    assert isinstance(source, dict)
    git_source = urlsplit(source["git"])
    assert f"{git_source.scheme}://{git_source.netloc}{git_source.path}" == (
        UPSTREAM_REPOSITORY
    )
    assert parse_qs(git_source.query) == {"rev": [UPSTREAM_SHA]}
    assert git_source.fragment == UPSTREAM_SHA


def test_lock_is_frozen_and_offline_checkable():
    result = subprocess.run(
        ["uv", "lock", "--check", "--offline"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_installed_cnequity_metadata_and_module_version_match_pin():
    assert metadata.version("cnequity") == CNEQUITY_VERSION

    cnequity = importlib.import_module("cnequity")
    assert cnequity.__version__ == CNEQUITY_VERSION


def test_installed_cnequity_direct_url_proves_immutable_git_origin():
    distribution = metadata.distribution("cnequity")
    direct_url_text = distribution.read_text("direct_url.json")

    assert direct_url_text is not None
    direct_url = json.loads(direct_url_text)
    assert direct_url["url"] == UPSTREAM_REPOSITORY
    assert "archive_info" not in direct_url
    assert direct_url.get("dir_info", {}).get("editable") is not True

    vcs_info = direct_url.get("vcs_info")
    assert isinstance(vcs_info, dict)
    assert vcs_info.get("vcs") == "git"
    assert vcs_info.get("commit_id") == UPSTREAM_SHA
