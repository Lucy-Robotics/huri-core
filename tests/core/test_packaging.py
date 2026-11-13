"""The [server]/[client] split: a client never pulls in Ray or FastAPI, neither
when it installs huri-core[client] nor when a client-side file runs (#155)."""

import ast
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Iterator, Optional, Set

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from huri_core.core.manifest import loads_toml  # tomli on Python 3.10

ROOT = Path(__file__).resolve().parents[2]
PROJECT = loads_toml((ROOT / "pyproject.toml").read_text())["project"]
EXTRAS = PROJECT["optional-dependencies"]
SERVER_ONLY = {"ray", "fastapi"}
# Manifest ranges are checked by the server only (startup and sessions).
NOT_FOR_CLIENTS = SERVER_ONLY | {"packaging", "tomli"}

# What a client-side program imports: the base classes and the client SDK, the
# `huri` command, huri_core.testing, and what they import (cli -> plugins ->
# manifest).
CLIENT_IMPORTS = [
    "huri_core",
    "huri_core.core",
    "huri_core.core.events",
    "huri_core.core.module",
    "huri_core.core.bus",
    "huri_core.core.session",
    "huri_core.core.client",
    "huri_core.core.dataclasses.config",
    "huri_core.core.user_config",
    "huri_core.core.plugins",
    "huri_core.core.manifest",
    "huri_core.cli",
    "huri_core.testing",
]
# What a client-side file must never import, even inside a function: the
# server stack, and huri-core's own server side.
SERVER_STACK = {
    "ray",
    "fastapi",
    "huri_core.app",
    "huri_core.launch_huri",
    "huri_core.core.app",
    "huri_core.core.compat",
    "huri_core.core.factory",
    "huri_core.core.huri",
    "huri_core.core.sender",
}


def resolve(extra: str) -> list[Requirement]:
    """Requirements of an extra, following huri-core[...] self-references."""
    reqs = []
    for raw in EXTRAS[extra]:
        req = Requirement(raw)
        if canonicalize_name(req.name) == canonicalize_name(PROJECT["name"]):
            for sub in req.extras:
                reqs += resolve(sub)
        else:
            reqs.append(req)
    return reqs


def names(reqs: list[Requirement]) -> set[str]:
    return {canonicalize_name(r.name) for r in reqs}


def test_base_install_has_no_dependency():
    assert PROJECT["dependencies"] == []


def test_client_extras_exclude_server_stack():
    assert not names(resolve("client")) & NOT_FOR_CLIENTS


def test_server_extra_owns_the_serve_pins():
    pinned = {canonicalize_name(r.name): r for r in resolve("server")}
    assert {"ray", "fastapi", "protobuf", "setuptools"} <= pinned.keys()
    assert "4.25.8" in pinned["protobuf"].specifier


def test_client_side_imports_without_the_server_stack():
    # A None entry in sys.modules makes `import ray` raise ImportError, as if
    # only huri-core[client] were installed.
    code = (
        "import sys\n"
        + "".join(f"sys.modules[{m!r}] = None\n" for m in sorted(NOT_FOR_CLIENTS))
        + "".join(f"import {m}\n" for m in CLIENT_IMPORTS)
        + "assert huri_core.cli.main(['--help']) == 0\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True
    )
    assert res.returncode == 0, res.stderr


def source_path(module: str) -> Path:
    path = ROOT.joinpath(*module.split("."))
    return path / "__init__.py" if path.is_dir() else path.with_suffix(".py")


def imports(module: str, source: str) -> Iterator[str]:
    """Dotted names `module` imports, at any depth (inside functions too), with
    relative imports resolved. `if TYPE_CHECKING:` bodies are skipped: they
    never run."""
    package = module.split(".")
    if source_path(module).name != "__init__.py":
        package = package[:-1]

    def walk(nodes: Iterable[ast.AST]) -> Iterator[str]:
        for node in nodes:
            if isinstance(node, ast.If) and ast.unparse(node.test) in (
                "TYPE_CHECKING",
                "typing.TYPE_CHECKING",
            ):
                yield from walk(node.orelse)
                continue
            if isinstance(node, ast.Import):
                yield from (alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parent = package[: len(package) - node.level + 1]
                    base = ".".join(parent + ([base] if base else []))
                yield base
                # `from huri_core.core import huri` imports a module too.
                yield from (f"{base}.{alias.name}" for alias in node.names)
            yield from walk(ast.iter_child_nodes(node))

    yield from walk(ast.parse(source).body)


def boundary_violations(module: str, source: Optional[str] = None) -> Set[str]:
    if source is None:
        source = source_path(module).read_text()
    rel = source_path(module).relative_to(ROOT)
    return {
        f"{rel} imports {server}"
        for name in imports(module, source)
        for server in SERVER_STACK
        if name == server or name.startswith(server + ".")
    }


def client_side_files() -> Set[str]:
    """CLIENT_IMPORTS and every huri_core module they import, at any depth, so
    a new file imported by a client-side one is checked without being listed."""
    found: Set[str] = set()
    todo = list(CLIENT_IMPORTS)
    while todo:
        module = todo.pop()
        if module in found or not source_path(module).is_file():
            continue
        found.add(module)
        todo += [
            name
            for name in imports(module, source_path(module).read_text())
            if name.startswith("huri_core.") and name not in SERVER_STACK
        ]
    return found


def test_a_file_imported_by_a_client_side_file_is_checked_too():
    assert "huri_core.core.policy" in client_side_files()  # via bus and session


def test_the_current_tree_passes_the_client_import_boundary():
    found = set().union(*(boundary_violations(m) for m in client_side_files()))
    assert not found, sorted(found)


@pytest.mark.parametrize(
    "module,target,source",
    [
        ("huri_core.core.client", "ray", "def f():\n    import ray\n"),
        ("huri_core.core.client", "ray", "if not TYPE_CHECKING:\n    import ray\n"),
        (
            "huri_core.core.bus",
            "huri_core.core.huri",
            "def f():\n    from huri_core.core.huri import HuRI\n",
        ),
        ("huri_core.core.session", "fastapi", "from fastapi import WebSocket\n"),
        (
            "huri_core.testing",
            "huri_core.core.factory",
            "from huri_core.core.factory import ModuleFactory\n",
        ),
        (
            "huri_core.core.plugins",
            "huri_core.core.compat",
            "from .compat import check_modules\n",
        ),
        (
            "huri_core.core",
            "huri_core.core.huri",
            "from . import huri\n",
        ),
    ],
)
def test_a_server_import_in_a_client_side_file_fails_ci(module, target, source):
    rel = source_path(module).relative_to(ROOT)
    assert boundary_violations(module, source) == {f"{rel} imports {target}"}


def test_an_annotation_only_import_of_the_server_stack_is_allowed():
    source = source_path("huri_core.core.module").read_text()
    assert "if TYPE_CHECKING:" in source and "from ray.serve import" in source
    assert not boundary_violations("huri_core.core.module")


def test_a_guarded_optional_import_of_a_server_only_helper_library_is_allowed():
    source = source_path("huri_core.core.manifest").read_text()
    assert "packaging.specifiers" in imports("huri_core.core.manifest", source)
    assert not boundary_violations("huri_core.core.manifest")


def constraints() -> dict[str, Requirement]:
    reqs = {}
    for line in (ROOT / "constraints.txt").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            req = Requirement(line)
            reqs[canonicalize_name(req.name)] = req
    return reqs


def test_constraints_only_pin_what_huri_core_requires():
    """constraints.txt is core's: a module's pins belong in huri-modules."""
    required = {canonicalize_name(r.name) for extra in EXTRAS for r in resolve(extra)}
    assert constraints().keys() <= required, constraints().keys() - required


def test_extras_agree_with_constraint_pins():
    """constraints.txt pins exact versions; extras must accept those pins."""
    pins = {
        name: str(next(iter(req.specifier))).removeprefix("==")
        for name, req in constraints().items()
        if re.fullmatch(r"==[^,*]+", str(req.specifier))
    }
    assert pins, "constraints.txt pins nothing exactly"
    for extra in EXTRAS:
        for req in resolve(extra):
            version = pins.get(canonicalize_name(req.name))
            if version and req.specifier:
                assert version in req.specifier, f"[{extra}] {req} vs =={version}"


def test_the_package_is_typed():
    # PEP 561: without py.typed in the wheel, type checkers of an installed
    # huri-core (huri-modules' CI) see it as untyped. CI's build job checks
    # the wheel itself.
    tool = loads_toml((ROOT / "pyproject.toml").read_text())["tool"]
    assert (ROOT / "huri_core" / "py.typed").is_file()
    assert "py.typed" in tool["setuptools"]["package-data"]["huri_core"]
