"""huri-core's public surface (docs/ARCHITECTURE.md, Versions) is recorded in
public_api.txt. Any change to it fails here until the record is regenerated,
so the pull request shows the change and its reviewer picks the version bump
(#156). Regenerate with:

    python -m tests.core.test_public_api
"""

import dataclasses
import difflib
import importlib
import inspect
import re
import sys
from pathlib import Path
from typing import Any, List

import pytest

RECORD = Path(__file__).with_name("public_api.txt")
REGENERATE = "python -m tests.core.test_public_api"

# What module packages, event packages, use cases and `huri` commands may use.
# Left out on purpose: session, factory, sender and huri, the server's
# internals (huri-modules' integration tests use them at one pinned huri-core
# ref, and its canary against `dev` shows their changes). The bus's EventGraph
# is in: event policies call its `dispatch` (RELEASING.md).
PUBLIC = {
    "huri_core.core.events": ["EventData", "JsonEvent", "BytesEvent"],
    "huri_core.core.module": ["Module", "ModuleWithHandle", "ModuleWithId"],
    "huri_core.core.dataclasses.config": ["ModuleConfig", "ClientConfig", "LogConfig"],
    "huri_core.core.client": [
        "HuRIClient",
        "HuRISessionError",
        "ConnectionMessage",  # its `status` records the ConnectionStatus values
        "load_raw_client_config",
        "load_client_config",
        "encode",
        "decode",
    ],
    "huri_core.testing": None,  # its __all__
    "huri_core.core.manifest": [
        "MANIFEST_FILE",
        "ManifestError",
        "Port",
        "ModuleManifest",
        "loads_toml",
        "loads_manifest",
        "load_manifest",
    ],
    "huri_core.core.plugins": [
        "MODULES_GROUP",
        "EVENTS_GROUP",
        "CURATED_NAMESPACE",
        "PluginError",
        "topic_namespace",
        "parse_selection",
        "installed_modules",
        "load_modules",
        "load_events",
        "find_manifest",
        "EVENT_POLICIES_GROUP",
        "installed_event_policies",
        "load_event_policy",
    ],
    # EventPolicy is the plugin interface; InterruptPolicy's constructor is the
    # client's `event_policy:` schema.
    "huri_core.core.policy": ["EventPolicy", "InterruptPolicy"],
    "huri_core.core.bus": ["EventGraph"],
    "huri_core.core.compat": [
        "CompatibilityError",
        "check_modules",
        "check_manifests",
        "check_actuation",
        "check_session",
    ],
    "huri_core.cli": ["COMMANDS_GROUP", "main"],
    "huri_core.app": ["build_app"],
}


def signature(obj: Any) -> str:
    # A default that is a function prints its address: drop it.
    return re.sub(r" at 0x[0-9a-f]+", "", str(inspect.signature(obj)))


def function(name: str, obj: Any) -> str:
    prefix = "async def" if inspect.iscoroutinefunction(obj) else "def"
    return f"{prefix} {name}{signature(obj)}"


def describe_class(qual: str, cls: type) -> List[str]:
    bases = vars(cls).get("__orig_bases__", cls.__bases__)  # keeps Generic[...]
    lines = [f"class {qual}({', '.join(map(inspect.formatannotation, bases))})"]
    if dataclasses.is_dataclass(cls):
        lines.append(f"{qual}: dataclass{signature(cls)}")
    for attr, ann in vars(cls).get("__annotations__", {}).items():
        if not attr.startswith("_"):
            lines.append(f"{qual}.{attr}: {inspect.formatannotation(ann)}")
    for attr, raw in vars(cls).items():
        if attr.startswith("_") and attr != "__init__":
            continue
        if dataclasses.is_dataclass(cls) and attr == "__init__":
            continue  # the dataclass line above
        if isinstance(raw, (classmethod, staticmethod)):
            kind = type(raw).__name__
            lines.append(f"{function(f'{qual}.{attr}', getattr(cls, attr))}  [{kind}]")
        elif isinstance(raw, property):
            lines.append(f"{qual}.{attr}  [property]")
        elif callable(raw):
            lines.append(function(f"{qual}.{attr}", raw))
        else:
            lines.append(f"{qual}.{attr} = {raw!r}")
    return lines


def surface() -> List[str]:
    lines = []
    for module_name, names in PUBLIC.items():
        module = importlib.import_module(module_name)
        for name in names if names is not None else module.__all__:
            obj = getattr(module, name)
            qual = f"{module_name}.{name}"
            if inspect.isclass(obj):
                lines += describe_class(qual, obj)
            elif callable(obj):
                lines.append(function(qual, obj))
            else:
                lines.append(f"{qual} = {obj!r}")
    return lines


def recorded(path: Path = RECORD) -> List[str]:
    return [line for line in path.read_text().splitlines() if not line.startswith("#")]


def write(path: Path = RECORD) -> None:
    header = f"# huri-core's public surface; regenerate with: {REGENERATE}\n"
    path.write_text(header + "\n".join(surface()) + "\n")


def difference(record: List[str], current: List[str]) -> str:
    if record == current:
        return ""
    diff = difflib.unified_diff(record, current, "recorded", "current", lineterm="")
    return (
        "huri-core's public surface changed:\n"
        + "\n".join(diff)
        + f"\n\nIf this is intended, run `{REGENERATE}` and commit "
        "tests/core/public_api.txt; a change that breaks callers needs a minor "
        "bump (docs/ARCHITECTURE.md, Versions)."
    )


def test_an_unchanged_public_surface_passes():
    message = difference(recorded(), surface())
    assert not message, message


@pytest.fixture
def renamed_config(monkeypatch):
    """Replaces ClientConfig by `fields`, with the real one's qualified name."""
    from huri_core.core.dataclasses import config

    def replace(fields):
        cls = dataclasses.make_dataclass("ClientConfig", fields)
        cls.__module__ = config.__name__
        monkeypatch.setattr(config, "ClientConfig", cls)

    return replace


def test_renaming_a_clientconfig_field_fails_ci_with_the_difference(renamed_config):
    renamed_config(
        [("user_id", str), ("modules", dict), ("topics", list), ("send", list)]
    )
    message = difference(recorded(), surface())
    lines = message.splitlines()
    assert any(line.startswith("-") and "subscribe" in line for line in lines)
    assert any(line.startswith("+") and "topics" in line for line in lines)
    assert REGENERATE in message


def test_adding_a_public_name_fails_until_the_record_is_regenerated(monkeypatch):
    import huri_core.testing as testing

    class FakeClock:
        def now(self) -> float:
            return 0.0

    monkeypatch.setattr(testing, "FakeClock", FakeClock, raising=False)
    monkeypatch.setattr(testing, "__all__", [*testing.__all__, "FakeClock"])
    assert difference(recorded(), surface())


def test_a_regenerated_record_shows_the_change_in_the_pull_request(
    renamed_config, tmp_path
):
    before = recorded()
    renamed_config(
        [
            ("user_id", str),
            ("modules", dict),
            ("subscribe", list),
            ("send", list),
            ("locale", str, dataclasses.field(default="fr-FR")),
        ]
    )
    record = tmp_path / "public_api.txt"
    write(record)
    assert not difference(recorded(record), surface())
    added = [line for line in difflib.ndiff(before, recorded(record)) if line[0] == "+"]
    assert any("locale" in line for line in added)


def test_changing_a_private_helper_leaves_the_record_unchanged(monkeypatch):
    from huri_core.core import plugins

    monkeypatch.setattr(plugins, "_dist", lambda ep, verbose=False: "?")
    assert not difference(recorded(), surface())


@pytest.mark.parametrize(
    "module_name,constant,group",
    [
        ("huri_core.core.plugins", "MODULES_GROUP", "huri.modules"),
        ("huri_core.core.plugins", "EVENTS_GROUP", "huri.events"),
        ("huri_core.cli", "COMMANDS_GROUP", "huri.commands"),
    ],
)
def test_entry_point_group_names_are_part_of_the_public_surface(
    monkeypatch, module_name, constant, group
):
    module = importlib.import_module(module_name)
    assert getattr(module, constant) == group
    monkeypatch.setattr(module, constant, group + "_v2")
    assert difference(recorded(), surface())


if __name__ == "__main__":
    write()
    print(f"wrote {RECORD.relative_to(Path.cwd())}", file=sys.stderr)
