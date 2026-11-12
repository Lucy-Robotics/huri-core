"""A whole session through the server's websocket endpoint, with toy modules
and events: the handshake, the routing of JSON and binary frames, and the
rejections. No Ray cluster: the ingress class runs in-process on a fake socket.
"""

import asyncio
import json
import logging
import struct
from dataclasses import dataclass
from typing import Any, Dict

import pytest

from huri_core.core.events import BytesEvent, JsonEvent
from huri_core.core.huri import HuRI
from huri_core.core.module import Module
from huri_core.core.policy import EventPolicy, InterruptPolicy

# @serve.ingress subclasses the class it decorates (with an async __init__ for
# Serve); its direct base is HuRI as written, which runs without Serve.
Server = HuRI.func_or_class.__mro__[1]
assert "run_session" in vars(Server)


@dataclass
class Word(JsonEvent):
    text: str


class Chunk(BytesEvent):
    pass


class Upper(Module):
    input_type, output_type = "toy.word", "toy.loud"

    async def process(self, data: Word):
        return Word(data.text.upper())


class Reverse(Module):
    input_type, output_type = "toy.chunk", "toy.reversed"

    async def process(self, data: Chunk):
        return Chunk(data.data[::-1])


EVENTS = {
    "toy.word": Word,
    "toy.loud": Word,
    "toy.chunk": Chunk,
    "toy.reversed": Chunk,
}


def frame(topic: str, payload: bytes) -> bytes:
    return struct.pack("!H", len(topic)) + topic.encode() + payload


class FakeSocket:
    """The FastAPI WebSocket methods the endpoint uses. Replays `incoming`
    (handshake first), then disconnects."""

    def __init__(self, handshake, *incoming):
        self.handshake = handshake
        self.incoming = list(incoming)
        self.sent = []
        self.closed = None

    async def accept(self):
        pass

    async def receive_json(self):
        if isinstance(self.handshake, Exception):
            raise self.handshake
        return self.handshake

    async def receive(self):
        await asyncio.sleep(0.02)  # let the previous event go through the bus
        if not self.incoming:
            return {"type": "websocket.disconnect"}
        message = self.incoming.pop(0)
        key = "bytes" if isinstance(message, bytes) else "text"
        return {"type": "websocket.receive", key: message}

    async def send_json(self, message):
        self.sent.append(message)

    async def send_bytes(self, payload):
        self.sent.append(payload)

    async def close(self, code=None, reason=None):
        self.closed = code


def session(ws: FakeSocket, modules=None) -> FakeSocket:
    server = Server(
        modules or {"upper": Upper, "reverse": Reverse}, {}, dict(EVENTS), ()
    )
    return session_on(server, ws)


def session_on(server, ws: FakeSocket) -> FakeSocket:
    asyncio.run(asyncio.wait_for(server.run_session(ws), timeout=10))
    return ws


def handshake(modules, subscribe, send):
    return {"user_id": "u1", "modules": modules, "subscribe": subscribe, "send": send}


def test_json_and_binary_frames_go_through_the_pipeline_and_back():
    ws = session(
        FakeSocket(
            handshake(
                {"a": {"name": "upper"}, "b": {"name": "reverse"}},
                subscribe=["toy.loud", "toy.reversed"],
                send=["toy.word", "toy.chunk"],
            ),
            json.dumps({"topic": "toy.word", "data": {"text": "hi"}}),
            frame("toy.chunk", b"\x01\x02\x03"),
        )
    )
    assert ws.sent == [
        {"type": "session_init", "user_id": "u1"},
        {"topic": "toy.loud", "data": {"text": "HI"}},
        frame("toy.reversed", b"\x03\x02\x01"),
    ]


def test_a_frame_on_an_unknown_topic_is_dropped_not_fatal():
    ws = session(
        FakeSocket(
            handshake({"a": {"name": "upper"}}, ["toy.loud"], ["toy.word"]),
            json.dumps({"topic": "toy.nope", "data": {}}),
            json.dumps({"topic": "toy.word", "data": {"text": "still here"}}),
        )
    )
    assert ws.sent[-1] == {"topic": "toy.loud", "data": {"text": "STILL HERE"}}


def test_an_unknown_module_is_rejected_with_the_servers_modules():
    ws = session(FakeSocket(handshake({"a": {"name": "stt"}}, [], ["toy.word"])))
    (reply,) = ws.sent
    assert reply["type"] == "session_error"
    assert "Unknown module 'stt'" in reply["error"]
    assert reply["server_modules"] == ["reverse", "upper"]
    assert ws.closed == 1008


def test_an_incomplete_session_is_rejected_before_it_starts():
    ws = session(FakeSocket(handshake({"a": {"name": "upper"}}, ["toy.loud"], [])))
    (reply,) = ws.sent
    assert reply["type"] == "session_error"
    assert "Upper consumes 'toy.word'" in reply["error"]


def test_a_malformed_handshake_gets_a_session_error():
    """Not JSON, or not an object: still a session_error, never a dropped socket."""
    for handshake_ in (json.JSONDecodeError("bad", "x", 0), ["not", "an", "object"]):
        ws = session(FakeSocket(handshake_))
        assert ws.sent[0]["type"] == "session_error" and ws.closed == 1008


class Servo(Module):
    input_type, output_type = "toy.word", None
    actuation = True

    async def process(self, data: Word):
        return None


def get(server, path: str):
    """GET `path` on a FastAPI app routing to `server`'s endpoints, through ASGI:
    status and decoded JSON body."""

    from fastapi import FastAPI

    api = FastAPI()
    api.add_api_route("/modules", server.list_modules, methods=["GET"])
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("testclient", 1),
        "server": ("testserver", 80),
    }
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(api(scope, receive, send))
    status = messages[0]["status"]
    body = b"".join(m.get("body", b"") for m in messages[1:])
    return status, json.loads(body)


def app_routes(router):
    """Every route of `router`, including those of included routers."""

    for route in router.routes:
        inner = getattr(route, "original_router", None)  # an included router
        if inner is not None:
            yield from app_routes(inner)
        else:
            yield route


def test_the_ingress_declares_get_modules():
    from huri_core.core.app import app

    assert ("/modules", ("GET",)) in {
        (route.path, tuple(sorted(getattr(route, "methods", None) or ())))
        for route in app_routes(app)
    }


def test_the_ingress_route_serves_list_modules_unchanged():
    """The tests above mount `list_modules` on their own app: check that the
    real route is that endpoint, with nothing reshaping or guarding its answer
    (Serve binds the deployment instance to it at runtime)."""

    from huri_core.core.app import app

    (route,) = [r for r in app_routes(app) if r.path == "/modules"]
    assert route.endpoint.__name__ == "list_modules"
    assert route.response_model in (None, Dict[str, Any])
    assert not route.dependencies and not route.dependant.dependencies


def test_get_modules_lists_the_enabled_modules_their_topics_and_actuation():
    import huri_core

    server = Server(
        {"upper": Upper, "reverse": Reverse, "servo": Servo},
        {},
        dict(EVENTS),
        actuating=["servo"],
    )
    status, body = get(server, "/modules")
    assert status == 200
    assert body == {
        "modules": ["reverse", "servo", "upper"],
        "details": {
            "reverse": {
                "input": "toy.chunk",
                "output": "toy.reversed",
                "actuation": False,
                "distribution": None,
                "version": None,
            },
            "servo": {
                "input": "toy.word",
                "output": None,
                "actuation": True,
                "distribution": None,
                "version": None,
            },
            "upper": {
                "input": "toy.word",
                "output": "toy.loud",
                "actuation": False,
                "distribution": None,
                "version": None,
            },
        },
        "huri_core": huri_core.__version__,
    }
    # The same names this server tells a rejected session about.
    ws = session_on(
        server, FakeSocket(handshake({"a": {"name": "stt"}}, [], ["toy.word"]))
    )
    assert ws.sent[0]["type"] == "session_error"
    assert ws.sent[0]["server_modules"] == body["modules"]


def test_actuating_is_a_required_argument():
    """A binder that forgets it must fail, not report a servo as safe."""

    with pytest.raises(TypeError):
        Server({"servo": Servo}, {}, dict(EVENTS))


def test_get_modules_leaves_out_installed_but_disabled_modules(monkeypatch):
    import huri_core.core.huri as huri_module

    monkeypatch.setattr(huri_module, "installed_modules", lambda: ["upper", "tts"])
    server = Server({"upper": Upper}, {}, dict(EVENTS), ())
    _, body = get(server, "/modules")
    assert body["modules"] == ["upper"]
    assert list(body["details"]) == ["upper"]


def test_get_modules_imports_nothing(monkeypatch):
    """Answered from what the server registered at startup: no entry point is
    loaded, no module instantiated."""

    import huri_core.core.huri as huri_module
    import huri_core.core.plugins as plugins

    server = Server({"upper": Upper}, {}, dict(EVENTS), ())

    def forbidden(*args, **kwargs):
        raise AssertionError("GET /modules must not load or build a module")

    for target, name in [
        (plugins, "load_modules"),
        (plugins, "load_events"),
        (plugins, "installed_modules"),
        (huri_module, "installed_modules"),
    ]:
        monkeypatch.setattr(target, name, forbidden)
    monkeypatch.setattr(server.module_factory, "create", forbidden)
    monkeypatch.setattr(Upper, "__init__", forbidden)
    status, body = get(server, "/modules")
    assert status == 200 and body["modules"] == ["upper"]


# --- per-session log levels -------------------------------------------------


class Boom(Module):
    input_type, output_type = "toy.word", "toy.loud"

    async def process(self, data: Word):
        raise RuntimeError("kaput")


class Mic(Module):
    input_type, output_type = "huri.audio.in", None

    async def process(self, data: Chunk):
        return None


@pytest.fixture
def serve_log(caplog):
    """The server's log lines, whether or not importing ray cut the logger off
    from the root handler."""

    ray_logger = logging.getLogger("ray.serve")
    ray_logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger="ray.serve")
    yield caplog
    ray_logger.removeHandler(caplog.handler)


def word(text="hi"):
    return json.dumps({"topic": "toy.word", "data": {"text": text}})


def logged(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


def run(modules, log, *, subscribe=("toy.loud",), send=("toy.word",), incoming=()):
    config = handshake(modules, list(subscribe), list(send))
    if log is not None:
        config["log"] = log
    events = {**EVENTS, "huri.audio.in": Chunk}
    server = Server(
        {"upper": Upper, "reverse": Reverse, "boom": Boom, "mic": Mic},
        {},
        events,
        (),
    )
    return session_on(server, FakeSocket(config, *(incoming or (word(),))))


def test_a_module_set_to_warning_has_no_traffic_lines_in_the_server_log(serve_log):
    ws = run(
        {"shout": {"name": "upper"}, "back": {"name": "reverse"}},
        {"modules": {"shout": "WARNING", "back": "DEBUG"}},
        subscribe=("toy.loud", "toy.reversed"),
        send=("toy.word", "toy.chunk"),
        incoming=(word(), frame("toy.chunk", b"ab")),
    )
    assert {"topic": "toy.loud", "data": {"text": "HI"}} in ws.sent
    text = logged(serve_log)
    assert "upper (no distribution) -> 'toy.loud'" not in text
    assert "reverse (no distribution) -> 'toy.reversed'" in text


def test_a_topic_set_to_warning_has_no_traffic_lines_in_the_server_log(serve_log):
    run(
        {"shout": {"name": "upper"}},
        {"topics": {"toy.loud": "WARNING"}},
    )
    text = logged(serve_log)
    assert "publish topic='toy.loud'" not in text
    assert "upper (no distribution) -> 'toy.loud'" not in text
    assert "[Sender:toy.loud]" not in text
    assert "publish topic='toy.word'" in text


def test_log_levels_of_one_session_do_not_change_another_sessions_log(serve_log):
    run({"shout": {"name": "upper"}}, {"topics": {"toy.word": "WARNING"}})
    serve_log.clear()
    run({"shout": {"name": "upper"}}, None)
    assert "publish topic='toy.word'" in logged(serve_log)


def test_a_session_without_a_log_section_logs_as_before(serve_log):
    run({"shout": {"name": "upper"}}, None)
    text = logged(serve_log)
    assert "publish topic='toy.word'" in text
    assert "upper (no distribution) -> 'toy.loud'" in text
    assert "[Sender:toy.loud]" in text


def test_a_module_failure_is_logged_whatever_the_sessions_levels(serve_log):
    run({"bad": {"name": "boom"}}, {"modules": {"bad": "CRITICAL"}}, subscribe=())
    errors = [
        r
        for r in serve_log.records
        if "coroutine failed in boom (no distribution)" in r.message
    ]
    assert errors and errors[0].exc_info


def test_a_session_asking_for_debug_does_not_lower_the_servers_log_level():
    ray_logger = logging.getLogger("ray.serve")
    before = ray_logger.level
    ray_logger.setLevel(logging.INFO)
    try:
        run({"shout": {"name": "upper"}}, {"modules": {"shout": "DEBUG"}})
        assert ray_logger.level == logging.INFO
    finally:
        ray_logger.setLevel(before)


def test_a_session_config_with_an_unknown_log_level_is_rejected():
    ws = run({"shout": {"name": "upper"}}, {"modules": {"shout": "LOUD"}})
    (reply,) = ws.sent
    assert reply["type"] == "session_error" and "LOUD" in reply["error"]
    assert ws.closed == 1008


def test_a_session_config_with_the_log_level_of_a_module_it_does_not_use_is_rejected():
    ws = run({"shout": {"name": "upper"}}, {"modules": {"stt": "DEBUG"}})
    (reply,) = ws.sent
    assert reply["type"] == "session_error"
    assert "stt" in reply["error"] and "shout" in reply["error"]
    assert ws.closed == 1008


def mic_frame():
    return frame("huri.audio.in", b"\x00")


def test_microphone_audio_is_no_longer_silenced_by_a_hardcoded_rule(serve_log):
    run(
        {"m": {"name": "mic"}},
        None,
        subscribe=(),
        send=("huri.audio.in",),
        incoming=(mic_frame(),),
    )
    assert "publish topic='huri.audio.in'" in logged(serve_log)


def test_a_session_quiets_microphone_audio_through_its_log_section(serve_log):
    run(
        {"m": {"name": "mic"}},
        {"topics": {"huri.audio.in": "WARNING"}},
        subscribe=(),
        send=("huri.audio.in",),
        incoming=(mic_frame(),),
    )
    assert "publish topic='huri.audio.in'" not in logged(serve_log)


def test_a_server_logger_above_info_never_summarizes_module_outputs(monkeypatch):
    calls = []
    monkeypatch.setattr(Word, "summarize", lambda self: calls.append(1) or "x")
    ray_logger = logging.getLogger("ray.serve")
    before = ray_logger.level
    ray_logger.setLevel(logging.WARNING)
    try:
        ws = run({"shout": {"name": "upper"}}, None)
    finally:
        ray_logger.setLevel(before)
    assert {"topic": "toy.loud", "data": {"text": "HI"}} in ws.sent
    assert calls == []


class RecordingPolicy(EventPolicy):
    published = []

    async def publish(self, graph, topic, data):
        self.published.append(topic)
        await super().publish(graph, topic, data)


def test_swapping_the_policy_needs_no_module_change():
    RecordingPolicy.published = []
    server = Server(
        {"upper": Upper, "reverse": Reverse},
        {},
        dict(EVENTS),
        (),
        event_policy=RecordingPolicy,
    )
    ws = session_on(
        server,
        FakeSocket(
            handshake({"a": {"name": "upper"}}, ["toy.loud"], ["toy.word"]),
            json.dumps({"topic": "toy.word", "data": {"text": "hi"}}),
        ),
    )
    assert ws.sent[-1] == {"topic": "toy.loud", "data": {"text": "HI"}}
    assert "toy.word" in RecordingPolicy.published


def test_each_session_gets_its_own_interrupt_policy_by_default(monkeypatch):
    # Serve re-creates the class: its methods resolve names in their own globals.
    namespace = Server.run_session.__globals__
    created = []

    class Spy(namespace["Session"]):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self.event_graph._policy)

    monkeypatch.setitem(namespace, "Session", Spy)
    server = Server({"upper": Upper}, {}, dict(EVENTS), ())
    for _ in range(2):
        session_on(
            server,
            FakeSocket(handshake({"a": {"name": "upper"}}, ["toy.loud"], ["toy.word"])),
        )
    assert [type(p) for p in created] == [InterruptPolicy, InterruptPolicy]
    assert created[0] is not created[1]


# --- per-session interrupt rules ----------------------------------------------


class Stream(Module):
    """Streams a long answer: one token, then the rest once `release` is set."""

    input_type, output_type = "toy.word", "toy.token"
    release: asyncio.Event
    ended: asyncio.Event
    outcome: list

    async def process(self, data: Word):
        try:
            yield Word("first")
            await self.release.wait()
            yield Word("rest")
        except asyncio.CancelledError:
            self.outcome.append("cancelled")
            raise
        finally:
            self.ended.set()


class Halt(Module):
    """Releases the stream: without an interruption, it would go on."""

    input_type, output_type = "toy.stop", None

    async def process(self, data: Word):
        Stream.release.set()


class LiveSocket(FakeSocket):
    """A FakeSocket the test feeds frame by frame, telling it each frame sent."""

    def __init__(self, handshake):
        super().__init__(handshake)
        self.inbox: "asyncio.Queue[dict]" = asyncio.Queue()
        self.outbox: "asyncio.Queue[Any]" = asyncio.Queue()

    def push(self, text=None):
        message = (
            {"type": "websocket.disconnect"}
            if text is None
            else {"type": "websocket.receive", "text": text}
        )
        self.inbox.put_nowait(message)

    async def receive(self):
        return await self.inbox.get()

    async def send_json(self, message):
        await super().send_json(message)
        self.outbox.put_nowait(message)


def streaming_server(event_policy=InterruptPolicy):
    events = {**EVENTS, "toy.token": Word, "toy.stop": Word}
    return Server(
        {"stream": Stream, "halt": Halt}, {}, events, (), event_policy=event_policy
    )


def streaming_handshake(event_policy, subscribe=("toy.token",)):
    config = handshake(
        {"llm": {"name": "stream"}, "halt": {"name": "halt"}},
        subscribe=list(subscribe),
        send=["toy.word", "toy.stop"],
    )
    config["event_policy"] = event_policy
    return config


def test_urgent_event_cuts_short_a_running_less_urgent_stream():
    async def scenario():
        Stream.release, Stream.ended, Stream.outcome = (
            asyncio.Event(),
            asyncio.Event(),
            [],
        )
        ws = LiveSocket(
            streaming_handshake({"interrupts": {"toy.stop": ["toy.token"]}})
        )
        session_task = asyncio.create_task(streaming_server().run_session(ws))
        assert (await ws.outbox.get())["type"] == "session_init"
        ws.push(word("question"))
        assert (await ws.outbox.get())["data"] == {"text": "first"}

        ws.push(json.dumps({"topic": "toy.stop", "data": {"text": "stop"}}))
        await Stream.ended.wait()
        ws.push()
        await session_task
        return ws

    ws = asyncio.run(asyncio.wait_for(scenario(), timeout=10))
    assert Stream.outcome == ["cancelled"]
    tokens = [m["data"]["text"] for m in ws.sent if m.get("topic") == "toy.token"]
    assert tokens == ["first"]


@pytest.mark.parametrize(
    "event_policy, culprit",
    [
        ({"interrupts": {"acme.stopp": ["huri.token"]}}, "acme.stopp"),
        ({"interupts": {"acme.stop": ["huri.token"]}}, "interupts"),
    ],
)
def test_a_bad_event_policy_configuration_is_refused_at_the_handshake(
    event_policy, culprit
):
    ws = session_on(streaming_server(), FakeSocket(streaming_handshake(event_policy)))
    (reply,) = ws.sent
    assert reply["type"] == "session_error"
    assert culprit in reply["error"]
    assert ws.closed == 1008


def test_interrupt_rules_may_name_every_topic_of_the_session():
    """Module inputs and outputs, and the client's sent and subscribed topics."""

    rules = {"interrupts": {"toy.stop": ["toy.token", "toy.word"]}}
    ws = session_on(streaming_server(), FakeSocket(streaming_handshake(rules)))
    assert ws.sent[0]["type"] == "session_init"


def test_interrupt_rules_may_name_a_topic_the_client_does_not_subscribe_to():
    """Interrupting the LLM's tokens while the client only hears the TTS."""

    rules = {"interrupts": {"toy.stop": ["toy.token"]}}
    handshake_ = streaming_handshake(rules, subscribe=())
    ws = session_on(streaming_server(), FakeSocket(handshake_))
    assert ws.sent[0]["type"] == "session_init"


def test_interrupt_rules_sent_to_a_concurrent_server_are_refused():
    rules = {"interrupts": {"toy.stop": ["toy.token"]}}
    ws = session_on(
        streaming_server(EventPolicy), FakeSocket(streaming_handshake(rules))
    )
    (reply,) = ws.sent
    assert reply["type"] == "session_error"
    assert "interrupts" in reply["error"] and "EventPolicy" in reply["error"]


def test_a_concurrent_server_accepts_a_session_without_rules():
    ws = session_on(streaming_server(EventPolicy), FakeSocket(streaming_handshake({})))
    assert ws.sent[0]["type"] == "session_init"
