"""Where each module comes from: its distribution and version, from the loader
to the startup log, GET /modules and the session's bus log lines, and the
`huri.audit` logger's default routing."""

import asyncio
import importlib
import json
import logging
from itertools import count
from pathlib import Path

import pytest

from huri_core.core.events import JsonEvent
from huri_core.core.module import Module
from tests.core.test_server_session import (
    EVENTS,
    FakeSocket,
    Reverse,
    Server,
    Upper,
    get,
    handshake,
    session_on,
)

STT = "stt (huri-module-stt 0.3.1)"
SERVO = "servo (acme-servo 0.1.0)"
ECHO = f"{__name__}:Echo"


class Voice(JsonEvent):
    pass


class Transcript(JsonEvent):
    pass


class Angles(JsonEvent):
    pass


class Stt(Module):
    input_type, output_type = "acme.voice", "acme.transcript"


class Servo(Module):
    input_type, output_type = "acme.transcript", "acme.angles"
    actuation = True


class Echo(Module):
    input_type, output_type = "acme.voice", None


class Arm(Module):
    """Emits toy.angles for the in-process server tests."""

    input_type, output_type = "toy.word", "toy.angles"

    async def process(self, data):
        return type(data)("a")


def dist_info(root: Path, name: str, version: str, entry_points: str) -> None:
    info = root / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    )
    (info / "entry_points.txt").write_text(entry_points)


@pytest.fixture
def installed_packages(tmp_path, monkeypatch):
    here = __name__
    dist_info(
        tmp_path, "huri-module-stt", "0.3.1", f"[huri.modules]\nstt = {here}:Stt\n"
    )
    dist_info(
        tmp_path, "acme-servo", "0.1.0", f"[huri.modules]\nservo = {here}:Servo\n"
    )
    dist_info(
        tmp_path,
        "acme-events",
        "0.1.0",
        "[huri.events]\n"
        f"acme.voice = {here}:Voice\n"
        f"acme.transcript = {here}:Transcript\n"
        f"acme.angles = {here}:Angles\n",
    )
    monkeypatch.syspath_prepend(tmp_path)
    importlib.invalidate_caches()


@pytest.fixture
def build_app(installed_packages, monkeypatch, graph_logs):
    monkeypatch.setenv("HURI_MODULES", f"stt,servo,{ECHO}")
    monkeypatch.setenv("HURI_ACTUATION_MODULES", "servo")
    # importing huri_core.app builds the app with the ambient env
    with monkeypatch.context() as ambient:
        ambient.delenv("HURI_MODULES")
        ambient.delenv("HURI_ACTUATION_MODULES", raising=False)
        from huri_core import app
    return app.build_app


def startup_line(caplog) -> str:
    (line,) = [
        r.getMessage() for r in caplog.records if "enabled modules" in r.getMessage()
    ]
    return line


def test_startup_lists_each_enabled_modules_package_and_version(build_app, caplog):
    build_app()
    line = startup_line(caplog)
    assert "[HuRI] enabled modules (HURI_MODULES): " in line
    assert STT in line and SERVO in line


def test_the_app_binds_the_modules_distributions(build_app, monkeypatch):
    from huri_core import app

    bound = []

    class FakeHuRI:
        bind = staticmethod(lambda *args: bound.append(args))

    monkeypatch.setattr(app, "HuRI", FakeHuRI)
    build_app()
    assert bound[0][4] == {
        "stt": ("huri-module-stt", "0.3.1"),
        "servo": ("acme-servo", "0.1.0"),
        ECHO: (None, None),
    }


def test_an_unpackaged_module_is_listed_without_a_version(build_app, caplog):
    build_app()
    assert f"{ECHO} (no distribution)" in startup_line(caplog)


def test_get_modules_reports_each_modules_package_and_version():
    server = Server(
        {"upper": Upper, "echo": Reverse},
        {},
        dict(EVENTS),
        (),
        distributions={"upper": ("huri-module-stt", "0.3.1")},
    )
    status, body = get(server, "/modules")
    assert status == 200
    assert body["details"]["upper"]["distribution"] == "huri-module-stt"
    assert body["details"]["upper"]["version"] == "0.3.1"
    assert body["details"]["echo"]["distribution"] is None
    assert body["details"]["echo"]["version"] is None


def graph_lines(caplog):
    return [r.getMessage() for r in caplog.records if "[GRAPH]" in r.getMessage()]


def stt_server(**kwargs):
    return Server(
        {"stt": Upper},
        {},
        dict(EVENTS),
        (),
        distributions={"stt": ("huri-module-stt", "0.3.1")},
        **kwargs,
    )


def say(user: str, word: str) -> FakeSocket:
    shake = handshake({"a": {"name": "stt"}}, ["toy.loud"], ["toy.word"])
    shake["user_id"] = user
    return FakeSocket(shake, json.dumps({"topic": "toy.word", "data": {"text": word}}))


def test_pipeline_log_lines_name_the_session_and_the_module_version(
    graph_logs, monkeypatch
):
    import huri_core.core.huri as huri_module

    monkeypatch.setattr(huri_module.uuid, "uuid4", lambda: "sess-1")
    session_on(stt_server(), say("u1", "hello"))
    emitted = [line for line in graph_lines(graph_logs) if "HELLO" in line]
    assert emitted
    assert all("session=sess-1" in line and STT in line for line in emitted)


def test_two_sessions_log_lines_can_be_told_apart(graph_logs, monkeypatch):
    import huri_core.core.huri as huri_module

    ids = count(1)
    monkeypatch.setattr(huri_module.uuid, "uuid4", lambda: next(ids))
    server = stt_server()

    async def both():
        await asyncio.wait_for(
            asyncio.gather(
                server.run_session(say("lab-robot-1", "left")),
                server.run_session(say("lab-robot-2", "right")),
            ),
            timeout=10,
        )

    asyncio.run(both())
    lines = graph_lines(graph_logs)
    left = [line for line in lines if "LEFT" in line]
    right = [line for line in lines if "RIGHT" in line]
    assert left and right
    assert all("session=1 " in line for line in left)
    assert all("session=2 " in line for line in right)
    assert any("session=1 " in line for line in lines)
    assert any("session=2 " in line for line in lines)


def test_every_event_an_actuation_module_emits_is_audited_at_server_level(
    graph_logs, monkeypatch
):
    import huri_core.core.huri as huri_module

    monkeypatch.setattr(huri_module.uuid, "uuid4", lambda: "sess-9")
    server = Server(
        {"servo": Arm},
        {},
        {**EVENTS, "toy.angles": EVENTS["toy.word"]},
        ["servo"],
        distributions={"servo": ("acme-servo", "0.1.0")},
    )
    shake = handshake({"a": {"name": "servo"}}, [], ["toy.word"])
    shake["user_id"] = "lab-robot-1"
    ws = FakeSocket(shake, json.dumps({"topic": "toy.word", "data": {"text": "x"}}))
    session_on(server, ws)
    emitted = [
        r.getMessage()
        for r in graph_logs.records
        if r.name == "huri.audit" and "emitted" in r.getMessage()
    ]
    (line,) = emitted
    assert "session=sess-9" in line and "user=lab-robot-1" in line
    assert SERVO in line and "topic='toy.angles'" in line and "Word" in line


def test_audit_lines_reach_the_server_log_without_any_logging_setup(monkeypatch):
    audit = logging.getLogger("huri.audit")
    serve_logger = logging.getLogger("ray.serve")
    handler = logging.NullHandler()
    monkeypatch.setattr(audit, "handlers", [])
    monkeypatch.setattr(audit, "level", logging.NOTSET)
    monkeypatch.setattr(serve_logger, "handlers", [handler])  # what Serve attaches

    stt_server()
    assert audit.isEnabledFor(logging.INFO)
    assert audit.handlers == [handler]

    own = logging.NullHandler()
    monkeypatch.setattr(audit, "handlers", [own])
    stt_server()
    assert audit.handlers == [own]
