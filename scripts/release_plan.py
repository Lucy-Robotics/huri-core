"""Plan a release: what the Release workflow tags, publishes and uploads.

    python scripts/release_plan.py
    python scripts/release_plan.py next-dev X.Y.Z

Run by .github/workflows/release.yml, from a full clone (tags included):

- on a pull request into main (the release PR): checks the release is
  consistent, and fails it otherwise;
- on a push to main: a release when huri-core's version is not tagged yet,
  nothing otherwise (a merge into main that is not a release);
- on a manual run on main, once the version is tagged at this commit: resumes
  the upload.

Prints the plan (and adds it to the job summary), writes `release`,
`create`, `version` and `previous` to $GITHUB_OUTPUT, exits 1 on a problem.

`next-dev X.Y.Z` (run on a checkout of dev) prints the version dev should move
to after release X.Y.Z, `X.(Y+1).0.dev0`, or nothing when dev is already above it.
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional

from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]
OnPyPI = Callable[[str, str], bool]


def read_version() -> str:
    """`__version__` of huri_core, read as text: nothing is executed or installed."""

    text = (ROOT / "huri_core" / "__init__.py").read_text()
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if not match:
        raise SystemExit("no __version__ in huri_core/__init__.py")
    return match.group(1)


def next_dev(released: str, current: str) -> Optional[str]:
    """The version dev moves to after `released`; None if `current` is above it."""

    if Version(current) > Version(released):
        return None
    return f"{Version(released).major}.{Version(released).minor + 1}.0.dev0"


def on_pypi(name: str, version: str) -> bool:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=30):
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def release_tags(tags: List[str]) -> List[Version]:
    """The release tags (`X.Y.Z`, no pre-release), oldest first."""

    versions = []
    for tag in tags:
        try:
            version = Version(tag)
        except InvalidVersion:
            continue
        if not version.is_prerelease and str(version) == tag:
            versions.append(version)
    return sorted(versions)


def problems(core: str, tags: List[str], pypi: OnPyPI) -> List[str]:
    """Why releasing huri-core `core` now would be wrong."""

    found = []
    if str(Version(core)) != core:
        found.append(
            f"huri-core's version {core} is not canonical "
            f"({Version(core)}): write it as PEP 440 normalizes it"
        )
    if Version(core).is_prerelease:
        found.append(
            f"huri-core's version {core} is a pre-release: drop its pre-release "
            "suffix (.devN, rcN, ...) in huri_core/__init__.py"
        )
    if core in tags:
        found.append(f"tag {core} already exists: bump huri-core's version")
    elif pypi("huri-core", core):
        found.append(f"huri-core {core} is already on PyPI: bump huri-core's version")
    return found


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


def _output(**values: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.writelines(f"{k}={v}\n" for k, v in values.items())


def _summary(text: str) -> None:
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(text + "\n")


def main(pypi: OnPyPI = on_pypi) -> int:
    event = os.environ.get("GITHUB_EVENT_NAME", "pull_request")
    ref = os.environ.get("GITHUB_REF")
    if event != "pull_request" and ref != "refs/heads/main":
        # A tag is forever: only main's commits are ever released.
        print(f"::error::releases run on main only, not {ref}")
        _output(release="false")
        return 1
    core = read_version()

    tags = _git("tag", "--list").stdout.split()
    # The release before this one (a patch on an older line compares to its own).
    released = [str(v) for v in release_tags(tags) if v < Version(core)]
    previous: Optional[str] = released[-1] if released else None

    if core in tags:
        tagged = _git("rev-list", "-n", "1", core).stdout.strip()
        head = _git("rev-parse", "HEAD").stdout.strip()
        if event == "push":
            _summary(f"huri-core {core} is already tagged: not a release.")
            _output(release="false")
            return 0
        if event == "workflow_dispatch" and tagged == head:
            # Resume: the tag and the GitHub release exist, upload if missing.
            _output(
                release="true", create="false", version=core, previous=previous or ""
            )
            _summary(f"Resuming the upload of release {core}.")
            return 0

    found = problems(core, tags, pypi)
    if found:
        for problem in found:
            print(f"::error::{problem}")
        _summary("Release blocked:\n" + "\n".join(f"- {p}" for p in found))
        _output(release="false")
        return 1

    _summary(
        "\n".join(
            [
                f"Merging this into main releases huri-core **{core}**"
                + (f" (previous: {previous})" if previous else "")
                + ":",
                "",
                "| what | action |",
                "|---|---|",
                f"| tag `{core}` on main's merge commit | create |",
                f"| GitHub release `{core}` | publish |",
                f"| PyPI `huri-core {core}` | upload |",
            ]
        )
    )
    _output(
        release=json.dumps(event != "pull_request"),
        create="true",
        version=core,
        previous=previous or "",
    )
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["next-dev"]:
        print(next_dev(sys.argv[2], read_version()) or "")
        sys.exit(0)
    sys.exit(main())
