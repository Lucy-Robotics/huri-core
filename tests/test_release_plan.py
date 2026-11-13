"""scripts/release_plan.py: what blocks a release, and what one tags and uploads
(offline: PyPI faked, a throwaway git repo for the tags)."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "release_plan", ROOT / "scripts" / "release_plan.py"
)
assert spec and spec.loader
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)

TAGS = ["0.4.0", "0.4.1"]


def pypi(*released):
    """A fake PyPI holding `released` ("name==version" strings)."""
    return lambda name, version: f"{name}=={version}" in released


# --- problems(): the release PR's checks ---


def test_a_consistent_release_has_no_problem():
    assert plan.problems("0.5.0", TAGS, pypi()) == []


def test_a_dev_version_is_refused():
    (problem,) = plan.problems("0.5.0.dev0", TAGS, pypi())
    assert "pre-release" in problem and ".devN" in problem


def test_a_non_canonical_version_is_refused():
    (problem,) = plan.problems("v0.5.0", TAGS, pypi())
    assert "not canonical (0.5.0)" in problem


def test_an_already_tagged_version_is_refused():
    (problem,) = plan.problems("0.4.1", TAGS, pypi("huri-core==0.4.1"))
    assert "tag 0.4.1 already exists" in problem


def test_a_version_already_on_pypi_is_refused():
    (problem,) = plan.problems("0.5.0", TAGS, pypi("huri-core==0.5.0"))
    assert "huri-core 0.5.0 is already on PyPI" in problem


def test_release_tags_are_the_final_versions_oldest_first():
    tags = ["0.4.1", "0.4.0", "0.5.0.dev0", "v0.0.9", "nightly", "0.10.0"]
    assert [str(v) for v in plan.release_tags(tags)] == ["0.4.0", "0.4.1", "0.10.0"]


# --- next_dev(): the version dev moves to after a release ---


def test_the_next_development_version_is_the_next_minor():
    assert plan.next_dev("0.5.0", "0.5.0.dev0") == "0.6.0.dev0"
    assert plan.next_dev("0.5.2", "0.5.0.dev0") == "0.6.0.dev0"
    assert plan.next_dev("0.5.0", "0.5.0") == "0.6.0.dev0"  # dev still at the release


def test_dev_already_above_the_release_needs_no_next_version():
    assert plan.next_dev("0.4.2", "0.5.0.dev0") is None
    assert plan.next_dev("0.5.0", "0.6.0.dev0") is None


# --- main(): the decision that tags and uploads ---


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def bump(repo, version):
    (repo / "huri_core").mkdir(exist_ok=True)
    (repo / "huri_core" / "__init__.py").write_text(
        f'"""huri_core."""\n__version__ = "{version}"\n'
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"release {version}")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Releases 0.4.0 and 0.4.1, tagged."""
    for key in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{key}_NAME", "release test")
        monkeypatch.setenv(f"GIT_{key}_EMAIL", "release@example.invalid")
    git(tmp_path, "init", "-q", "-b", "main")
    for version in TAGS:
        bump(tmp_path, version)
        git(tmp_path, "tag", version)
    monkeypatch.setattr(plan, "ROOT", tmp_path)
    return tmp_path


def run(tmp_path, monkeypatch, event, ref="refs/heads/main"):
    out, summary = tmp_path / "out.txt", tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    monkeypatch.setenv("GITHUB_REF", ref)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code = plan.main(pypi())
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
    return code, outputs, summary.read_text() if summary.exists() else ""


def test_the_version_is_read_without_importing_huri_core(repo):
    bump(repo, "0.5.0.dev0")
    assert plan.read_version() == "0.5.0.dev0"


def test_the_release_pr_shows_what_merging_it_will_publish(repo, monkeypatch):
    bump(repo, "0.5.0")
    code, out, summary = run(repo, monkeypatch, "pull_request", "refs/pull/1/merge")
    assert code == 0 and out["release"] == "false"
    assert (out["version"], out["previous"]) == ("0.5.0", "0.4.1")
    assert "| tag `0.5.0` on main's merge commit | create |" in summary
    assert "| GitHub release `0.5.0` | publish |" in summary
    assert "| PyPI `huri-core 0.5.0` | upload |" in summary


def test_a_blocked_release_pr_fails(repo, monkeypatch):
    code, out, summary = run(repo, monkeypatch, "pull_request")  # 0.4.1, tagged
    assert code == 1 and out == {"release": "false"}
    assert "tag 0.4.1 already exists" in summary


def test_merging_the_release_pr_releases_it(repo, monkeypatch):
    bump(repo, "0.5.0")
    code, out, _ = run(repo, monkeypatch, "push")
    assert code == 0
    assert (out["release"], out["create"], out["version"]) == ("true", "true", "0.5.0")


def test_a_merge_into_main_that_is_not_a_release_publishes_nothing(repo, monkeypatch):
    code, out, summary = run(repo, monkeypatch, "push")  # 0.4.1, tagged
    assert code == 0 and out == {"release": "false"}
    assert "not a release" in summary


def test_a_manual_run_at_the_tagged_commit_resumes_the_upload(repo, monkeypatch):
    bump(repo, "0.5.0")
    git(repo, "tag", "0.5.0")
    code, out, _ = run(repo, monkeypatch, "workflow_dispatch")
    assert code == 0
    assert (out["release"], out["create"], out["version"]) == ("true", "false", "0.5.0")
    assert out["previous"] == "0.4.1"


def test_a_manual_run_on_main_after_the_release_commit_is_refused(repo, monkeypatch):
    bump(repo, "0.5.0")
    git(repo, "tag", "0.5.0")
    git(repo, "commit", "-q", "--allow-empty", "-m", "a later merge")
    code, out, _ = run(repo, monkeypatch, "workflow_dispatch")
    assert code == 1 and out == {"release": "false"}


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_a_release_from_another_branch_is_refused(repo, monkeypatch, event):
    bump(repo, "0.5.0")
    code, out, _ = run(repo, monkeypatch, event, ref="refs/heads/dev")
    assert code == 1 and out == {"release": "false"}


def test_the_previous_release_of_a_patch_on_an_older_line_is_its_own(repo, monkeypatch):
    git(repo, "tag", "0.5.0")  # a newer line exists
    bump(repo, "0.4.2")
    _, out, _ = run(repo, monkeypatch, "pull_request")
    assert out["previous"] == "0.4.1"
