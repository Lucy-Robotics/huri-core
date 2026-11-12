import asyncio
import logging
from dataclasses import dataclass

import pytest

from huri_core.core.bus import EventGraph
from huri_core.core.dataclasses.config import LogConfig
from huri_core.core.events import BytesEvent, EventData, JsonEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class DummyModule:
    input_type = None
    output_type = None

    async def process(self, data):
        raise NotImplementedError


@dataclass
class DummyEvent(EventData):
    value: int


@dataclass
class Reading(JsonEvent):
    """A toy event: what a third-party sensor package could register."""

    sensor: str
    value: float


# ---------------------------------------------------------------------------
# wire formats
# ---------------------------------------------------------------------------


def test_json_event_round_trips_through_its_wire_dict():
    wire = Reading("thermo", 21.5).to_wire()
    assert wire == {"sensor": "thermo", "value": 21.5}
    assert Reading.from_wire(wire) == Reading("thermo", 21.5)


def test_bytes_event_is_its_payload_and_summarizes_its_length():
    event = BytesEvent.from_wire(b"\x00\x01\x02")
    assert event.to_wire() == b"\x00\x01\x02"
    assert event.summarize() == "BytesEvent(len:3)"


def test_base_event_has_no_wire_format():
    with pytest.raises(NotImplementedError):
        EventData().to_wire()


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


def test_register():
    graph = EventGraph()

    module = DummyModule()
    module.input_type = "input"

    graph.register(module)

    assert module in graph.subscribers["input"]


# ---------------------------------------------------------------------------
# publish()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_calls_subscriber(monkeypatch):
    graph = EventGraph()

    called = asyncio.Event()

    class Module(DummyModule):
        input_type = "input"

        async def process(self, data):
            called.set()
            return None

    module = Module()
    graph.register(module)

    await graph.publish("input", DummyEvent(1))

    await asyncio.wait_for(called.wait(), timeout=1)


# ---------------------------------------------------------------------------
# coroutine output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coroutine_output_published():
    graph = EventGraph()

    received = asyncio.Event()

    class Producer(DummyModule):
        input_type = "start"
        output_type = "next"

        async def process(self, data):
            return DummyEvent(42)

    class Consumer(DummyModule):
        input_type = "next"

        async def process(self, data):
            assert data.value == 42
            received.set()

    graph.register(Producer())
    graph.register(Consumer())

    await graph.publish("start", DummyEvent(0))

    await asyncio.wait_for(received.wait(), timeout=1)


# ---------------------------------------------------------------------------
# async generator output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_generator_output():
    graph = EventGraph()

    values = []

    class Producer(DummyModule):
        input_type = "start"
        output_type = "stream"

        async def process(self, data):
            yield DummyEvent(1)
            yield DummyEvent(2)
            yield DummyEvent(3)

    class Consumer(DummyModule):
        input_type = "stream"

        async def process(self, data):
            values.append(data.value)

    graph.register(Producer())
    graph.register(Consumer())

    await graph.publish("start", DummyEvent(0))

    await asyncio.sleep(0.1)

    assert values == [1, 2, 3]


# ---------------------------------------------------------------------------
# None outputs ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_none_output_not_published():
    graph = EventGraph()

    called = False

    class Producer(DummyModule):
        input_type = "start"
        output_type = "next"

        async def process(self, data):
            return None

    class Consumer(DummyModule):
        input_type = "next"

        async def process(self, data):
            nonlocal called
            called = True

    graph.register(Producer())
    graph.register(Consumer())

    await graph.publish("start", DummyEvent(0))
    await asyncio.sleep(0.05)

    assert not called


# ---------------------------------------------------------------------------
# recursive routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recursive_routing():
    graph = EventGraph()

    done = asyncio.Event()

    class First(DummyModule):
        input_type = "a"
        output_type = "b"

        async def process(self, data):
            return DummyEvent(data.value + 1)

    class Second(DummyModule):
        input_type = "b"

        async def process(self, data):
            assert data.value == 2
            done.set()

    graph.register(First())
    graph.register(Second())

    await graph.publish("a", DummyEvent(1))

    await asyncio.wait_for(done.wait(), timeout=1)


# ---------------------------------------------------------------------------
# observability: session id, module labels, audit
# ---------------------------------------------------------------------------

STT = "stt (huri-module-stt 0.3.1)"
SERVO = "servo (acme-servo 0.1.0)"


def _graph_lines(logs):
    return [r.getMessage() for r in logs.records if r.name == "ray.serve"]


def _audit_lines(logs):
    return [r.getMessage() for r in logs.records if r.name == "huri.audit"]


def _stt_graph(done):
    class Stt(DummyModule):
        input_type = "huri.audio.in"
        output_type = "huri.transcript"

        async def process(self, data):
            return DummyEvent(1)

    class Sink(DummyModule):
        input_type = "huri.transcript"

        async def process(self, data):
            done.set()

    graph = EventGraph("s-1", "alice", {Stt: STT})
    graph.register(Stt())
    graph.register(Sink())
    return graph


@pytest.mark.asyncio
async def test_pipeline_log_lines_name_the_session_and_the_module_version(graph_logs):
    done = asyncio.Event()
    graph = _stt_graph(done)

    await graph.publish("huri.audio.in", DummyEvent(0))
    await asyncio.wait_for(done.wait(), timeout=1)

    lines = _graph_lines(graph_logs)
    assert any(
        "session=s-1" in line and STT in line and "huri.transcript" in line
        for line in lines
    )
    assert any(
        "publish topic='huri.transcript'" in line and "session=s-1" in line
        for line in lines
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["coroutine", "generator", "call"])
async def test_a_failing_modules_traceback_names_its_package_version_and_session(
    graph_logs, kind
):
    class Failing(DummyModule):
        input_type = "voice"

        if kind == "coroutine":

            async def process(self, data):
                raise RuntimeError("boom")

        elif kind == "generator":

            async def process(self, data):
                raise RuntimeError("boom")
                yield  # pragma: no cover

        else:

            def process(self, data):
                raise RuntimeError("boom")

    graph = EventGraph("s-1", "alice", {Failing: STT})
    graph.register(Failing())

    await graph.publish("voice", DummyEvent(0))
    await asyncio.gather(*list(graph._tasks))

    errors = [r for r in graph_logs.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "session=s-1" in errors[0].getMessage()
    assert STT in errors[0].getMessage()
    assert errors[0].exc_info is not None


@pytest.mark.asyncio
async def test_every_event_an_actuation_module_emits_is_audited(graph_logs):
    class Servo(DummyModule):
        input_type = "go"
        output_type = "servo.angles"

        async def process(self, data):
            yield DummyEvent(7)
            yield None
            yield DummyEvent(8)

    done = asyncio.Event()

    class Sink(DummyModule):
        input_type = "servo.angles"
        seen = 0

        async def process(self, data):
            Sink.seen += 1
            if Sink.seen == 2:
                done.set()

    graph = EventGraph("s-1", "lab-robot-1", {Servo: SERVO}, {Servo})
    graph.register(Servo())
    graph.register(Sink())

    await graph.publish("go", DummyEvent(0))
    await asyncio.wait_for(done.wait(), timeout=1)

    emitted = [line for line in _audit_lines(graph_logs) if "emitted" in line]
    assert len(emitted) == 2
    for line in emitted:
        assert "session=s-1" in line
        assert "user=lab-robot-1" in line
        assert SERVO in line
        assert "topic='servo.angles'" in line
    assert "DummyEvent(value=7)" in emitted[0]


@pytest.mark.asyncio
async def test_every_event_an_actuation_module_receives_is_audited(graph_logs):
    done = asyncio.Event()

    class Driver(DummyModule):
        input_type = "servo.angles"

        async def process(self, data):
            done.set()

    graph = EventGraph("s-1", "lab-robot-1", {Driver: SERVO}, {Driver})
    graph.register(Driver())

    await graph.publish("servo.angles", DummyEvent(5))
    await asyncio.wait_for(done.wait(), timeout=1)

    lines = _audit_lines(graph_logs)
    assert len(lines) == 1
    assert "received" in lines[0]
    assert "session=s-1" in lines[0]
    assert "user=lab-robot-1" in lines[0]
    assert SERVO in lines[0]
    assert "topic='servo.angles'" in lines[0]
    assert "DummyEvent(value=5)" in lines[0]


@pytest.mark.asyncio
async def test_a_non_actuation_module_writes_no_audit_line(graph_logs):
    done = asyncio.Event()
    graph = _stt_graph(done)

    await graph.publish("huri.audio.in", DummyEvent(0))
    await asyncio.wait_for(done.wait(), timeout=1)

    assert _audit_lines(graph_logs) == []


@pytest.mark.asyncio
async def test_a_module_without_a_label_is_logged_by_its_class_name(graph_logs):
    class Plain(DummyModule):
        input_type = "in"
        output_type = "out"

        async def process(self, data):
            return DummyEvent(1)

    graph = EventGraph()
    graph.register(Plain())

    await graph.publish("in", DummyEvent(0))
    await asyncio.gather(*list(graph._tasks))

    lines = _graph_lines(graph_logs)
    assert any("session=- Plain -> 'out'" in line for line in lines)


@pytest.mark.asyncio
async def test_the_audit_trail_ignores_the_sessions_log_levels(graph_logs):
    class Servo(DummyModule):
        input_type = "go"
        output_type = "servo.angles"

        async def process(self, data):
            yield DummyEvent(7)

    done = asyncio.Event()

    class Sink(DummyModule):
        input_type = "servo.angles"

        async def process(self, data):
            done.set()

    servo = Servo()
    log = LogConfig(
        modules={"servo": logging.CRITICAL}, topics={"servo.angles": logging.CRITICAL}
    )
    graph = EventGraph(
        "s-1", "lab-robot-1", {Servo: SERVO}, {Servo}, log, {id(servo): "servo"}
    )
    graph.register(servo)
    graph.register(Sink())

    await graph.publish("go", DummyEvent(0))
    await asyncio.wait_for(done.wait(), timeout=1)

    audit = _audit_lines(graph_logs)
    assert [("received" in x, "emitted" in x) for x in audit] == [
        (True, False),
        (False, True),
    ]
    assert not [x for x in _graph_lines(graph_logs) if "[GRAPH]" in x and " -> " in x]
