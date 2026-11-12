"""The client SDK against a local websocket server standing in for HuRI."""

import asyncio
import json
import logging
from dataclasses import dataclass

import pytest
import websockets

from huri_core.core.client import (
    HuRIClient,
    HuRISessionError,
    decode,
    encode,
    load_client_config,
)
from huri_core.core.dataclasses.config import ClientConfig
from huri_core.core.events import BytesEvent, JsonEvent


@dataclass
class Text(JsonEvent):
    text: str


class FakeHuRI:
    """Accepts or rejects the handshake, records frames, sends scripted ones."""

    def __init__(self, reply=None, outgoing=()):
        self.reply = reply or {"type": "session_init", "user_id": "u-server"}
        self.outgoing = list(outgoing)
        self.handshake = None
        self.received = []

    async def handler(self, ws):
        self.handshake = json.loads(await ws.recv())
        await ws.send(json.dumps(self.reply))
        if self.reply["type"] != "session_init":
            await ws.close()
            return
        for frame in self.outgoing:
            await ws.send(frame)
        async for message in ws:
            self.received.append(decode(message))

    async def __aenter__(self):
        self._server = await websockets.serve(self.handler, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}"
        return self

    async def __aexit__(self, *_):
        self._server.close()
        await self._server.wait_closed()


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=10))


async def until(predicate):
    while not predicate():
        await asyncio.sleep(0.01)


def test_frames_round_trip():
    assert decode(encode("toy.token", Text("hi"))) == ("toy.token", {"text": "hi"})
    assert decode(encode("toy.audio", BytesEvent(b"\x01\x02"))) == (
        "toy.audio",
        b"\x01\x02",
    )


def test_handshake_declares_the_session_and_server_parses_it():
    async def scenario():
        async with FakeHuRI() as server:
            client = HuRIClient(
                server.url,
                modules={"rag": {"name": "rag", "args": {"tone": "formal"}}},
                sends=["toy.question"],
                user_id="u-1",
            )
            client.on("toy.token", Text, lambda e: asyncio.sleep(0))
            async with client:
                assert client.user_id == "u-server"
            return server.handshake

    handshake = run(scenario())
    assert handshake["subscribe"] == ["toy.token"]
    assert handshake["send"] == ["toy.question"]
    config = ClientConfig.from_dict(handshake)
    assert config.user_id == "u-1" and config.modules["rag"].key == "rag"


def test_rejected_session_raises_with_the_server_modules():
    reply = {"type": "session_error", "error": "nope", "server_modules": ["stt"]}

    async def scenario():
        async with FakeHuRI(reply=reply) as server:
            with pytest.raises(HuRISessionError) as exc:
                await HuRIClient(server.url, modules={}).connect()
            return exc.value

    error = run(scenario())
    assert error.error == "nope" and error.server_modules == ["stt"]


def test_events_are_sent_as_json_or_binary():
    async def scenario():
        async with FakeHuRI() as server:
            client = HuRIClient(
                server.url, modules={}, sends=["toy.question", "toy.audio"]
            )
            async with client:
                await client.send("toy.question", Text("hello"))
                await client.send("toy.audio", BytesEvent(b"\x00\x01"))
                await until(lambda: len(server.received) == 2)
            return server.received

    assert run(scenario()) == [
        ("toy.question", {"text": "hello"}),
        ("toy.audio", b"\x00\x01"),
    ]


def test_sending_an_undeclared_topic_is_refused():
    async def scenario():
        async with FakeHuRI() as server:
            async with HuRIClient(server.url, modules={}, sends=[]) as client:
                with pytest.raises(ValueError, match="sends"):
                    await client.send("toy.question", Text("x"))

    run(scenario())


def test_handlers_of_a_topic_run_in_order_and_survive_failures():
    frames = [encode("toy.token", Text(str(i))) for i in range(5)]

    async def scenario():
        seen = []

        async def slow_then_fast(event: Text):
            # Earlier events sleep longer: concurrent handlers would reorder.
            await asyncio.sleep(0.05 - 0.01 * int(event.text))
            if event.text == "2":
                raise RuntimeError("handler bug")
            seen.append(event.text)

        async with FakeHuRI(outgoing=frames) as server:
            client = HuRIClient(server.url, modules={})
            client.on("toy.token", Text, slow_then_fast)
            async with client:
                await until(lambda: len(seen) == 4)
        return seen

    assert run(scenario()) == ["0", "1", "3", "4"]


def _tokens():
    return [encode("huri.token", Text(t)) for t in ("Hel", "lo", "!")]


def test_a_handler_is_sequential_by_default():
    async def scenario():
        seen, state = [], {"active": 0, "max": 0}
        gate = asyncio.Event()

        async def handler(event: Text):
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
            seen.append(event.text)
            await gate.wait()
            state["active"] -= 1

        async with FakeHuRI(outgoing=_tokens()) as server:
            client = HuRIClient(server.url, modules={})
            client.on("huri.token", Text, handler)
            async with client:
                await until(lambda: len(seen) == 1)
                await asyncio.sleep(0.05)
                assert seen == ["Hel"]
                gate.set()
                await until(lambda: len(seen) == 3)
        return seen, state["max"]

    assert run(scenario()) == (["Hel", "lo", "!"], 1)


def test_a_concurrent_handler_handles_events_in_parallel():
    async def scenario():
        started = []
        release = asyncio.Event()

        async def handler(event: Text):
            started.append(event.text)
            await release.wait()

        async with FakeHuRI(outgoing=_tokens()) as server:
            client = HuRIClient(server.url, modules={})
            client.on("huri.token", Text, handler, concurrent=True)
            async with client:
                await asyncio.wait_for(until(lambda: len(started) == 3), 1)
                release.set()
        return started

    assert run(scenario()) == ["Hel", "lo", "!"]


def test_a_concurrent_handler_does_not_hold_up_an_ordered_handler_on_the_same_topic():
    async def scenario():
        ordered = []
        never = asyncio.Event()

        async def stuck(event: Text):
            await never.wait()

        async def collect(event: Text):
            ordered.append(event.text)

        async with FakeHuRI(outgoing=_tokens()) as server:
            client = HuRIClient(server.url, modules={})
            client.on("huri.token", Text, stuck, concurrent=True)
            client.on("huri.token", Text, collect)
            async with client:
                await asyncio.wait_for(until(lambda: len(ordered) == 3), 1)
        return ordered

    assert run(scenario()) == ["Hel", "lo", "!"]


def test_a_failing_concurrent_handler_does_not_stop_the_others(caplog):
    async def scenario():
        seen = []

        async def handler(event: Text):
            if event.text == "lo":
                raise RuntimeError("handler bug")
            seen.append(event.text)

        async with FakeHuRI(outgoing=_tokens()) as server:
            client = HuRIClient(server.url, modules={})
            client.on("huri.token", Text, handler, concurrent=True)
            with caplog.at_level(logging.ERROR, logger="huri.client"):
                async with client:
                    await until(lambda: len(seen) == 2)
                    await until(lambda: bool(caplog.records))
        return seen

    assert set(run(scenario())) == {"Hel", "!"}
    assert any("huri.token" in r.getMessage() for r in caplog.records)


def test_closing_the_client_cancels_concurrent_handlers_still_running():
    async def scenario():
        tasks = []
        never = asyncio.Event()

        async def handler(event: Text):
            tasks.append(asyncio.current_task())
            await never.wait()

        async with FakeHuRI(outgoing=_tokens()) as server:
            client = HuRIClient(server.url, modules={})
            client.on("huri.token", Text, handler, concurrent=True)
            await client.connect()
            await until(lambda: len(tasks) == 3)
            await asyncio.wait_for(client.close(), 1)
            # Checked before asyncio.run() shutdown cancels leftovers itself.
            assert len(tasks) == 3
            assert all(t.cancelled() for t in tasks)

    run(scenario())


def test_subscribing_after_connecting_is_refused():
    async def scenario():
        async with FakeHuRI() as server:
            async with HuRIClient(server.url, modules={}) as client:
                with pytest.raises(RuntimeError, match="before connecting"):
                    client.on("toy.token", Text, lambda e: asyncio.sleep(0))

    run(scenario())


def test_one_topic_cannot_be_decoded_as_two_classes():
    client = HuRIClient("ws://unused", modules={})
    client.on("toy.token", Text, lambda e: asyncio.sleep(0))
    with pytest.raises(ValueError, match="already subscribed"):
        client.on("toy.token", BytesEvent, lambda e: asyncio.sleep(0))


def test_old_client_config_format_gets_a_migration_hint():
    legacy = {"user_id": "u", "interface_path": "x:y", "senders": {}, "hooks": {}}
    with pytest.raises(ValueError, match="subscribe.*send"):
        ClientConfig.from_dict(legacy)


def session_with_log(log):
    return {"user_id": "u", "modules": {"shout": {"name": "shout"}}, "log": log}


def test_a_log_level_name_is_parsed_case_insensitively_and_round_trips():
    log = {"modules": {"shout": "debug"}, "topics": {"echo.loud": "Warning"}}

    config = ClientConfig.from_dict(session_with_log(log))

    assert config.log.modules == {"shout": logging.DEBUG}
    assert config.log.topics == {"echo.loud": logging.WARNING}
    expected = {"modules": {"shout": "DEBUG"}, "topics": {"echo.loud": "WARNING"}}
    assert config.log.to_dict() == expected
    assert ClientConfig.from_dict(session_with_log(expected)).log == config.log
    assert config.log.allows(logging.DEBUG, "echo.text", "shout")
    assert not config.log.allows(logging.INFO, "echo.loud", "shout")
    assert not config.log.allows(logging.INFO, "echo.loud")


@pytest.mark.parametrize("level", ["LOUD", 10, None])
def test_an_unknown_log_level_is_refused_by_the_config(level):
    with pytest.raises(ValueError, match=f"'?{level}'?.*DEBUG"):
        ClientConfig.from_dict(session_with_log({"topics": {"echo.loud": level}}))


@pytest.mark.parametrize(
    "log",
    ["DEBUG", [], "", 0, {"modules": ["shout"]}, {"modules": []}, {"topics": "DEBUG"}],
)
def test_a_log_section_of_the_wrong_shape_is_refused_by_the_config(log):
    with pytest.raises(ValueError, match="log"):
        ClientConfig.from_dict(session_with_log(log))


def test_the_log_level_of_an_unknown_module_id_is_refused_by_the_config():
    with pytest.raises(ValueError, match=r"'stt'.*\['shout'\]"):
        ClientConfig.from_dict(session_with_log({"modules": {"stt": "DEBUG"}}))


def test_the_log_level_of_a_numeric_module_id_is_accepted_by_the_config():
    raw = {"user_id": "u", "modules": {1: {"name": "shout"}}}
    raw["log"] = {"modules": {1: "DEBUG"}}

    assert ClientConfig.from_dict(raw).log.modules == {1: logging.DEBUG}


def test_the_log_level_of_module_id_zero_filters_its_lines():
    raw = {"user_id": "u", "modules": {0: {"name": "shout"}}}
    raw["log"] = {"modules": {0: "WARNING"}}

    log = ClientConfig.from_dict(raw).log

    assert not log.allows(logging.INFO, "t", 0)
    assert log.allows(logging.WARNING, "t", 0)


def test_a_config_without_log_filters_nothing():
    config = ClientConfig.from_dict({"user_id": "u", "modules": {}})

    assert config.log.to_dict() == {}
    assert config.log.allows(logging.DEBUG, "echo.loud", "shout")
    assert ClientConfig.from_dict(session_with_log(None)).log == config.log


def test_connect_kwargs_reach_websockets_and_lift_the_size_limit(monkeypatch):
    seen = {}

    async def fake_connect(url, **kwargs):
        seen.update(kwargs, url=url)
        raise ConnectionRefusedError("stop here")

    monkeypatch.setattr(websockets, "connect", fake_connect)
    with pytest.raises(ConnectionRefusedError):
        run(HuRIClient("ws://x", modules={}).connect())
    assert seen == {"url": "ws://x", "max_size": None}

    seen.clear()
    with pytest.raises(ConnectionRefusedError):
        client = HuRIClient(
            "wss://x", modules={}, connect_kwargs={"max_size": 2**20, "ssl": "ctx"}
        )
        run(client.connect())
    assert seen == {"url": "wss://x", "max_size": 2**20, "ssl": "ctx"}


def test_a_frame_larger_than_one_mib_is_received():
    big = "x" * (2 * 2**20)

    async def scenario():
        async with FakeHuRI(outgoing=[encode("toy.token", Text(big))]) as server:
            got = []
            client = HuRIClient(server.url, modules={})
            client.on("toy.token", Text, lambda e: _append(got, e))
            async with client:
                await until(lambda: got)
            return got

    assert run(scenario())[0].text == big


async def _append(target, item):
    target.append(item)


def _client_yaml(tmp_path, log=""):
    path = tmp_path / "client.yaml"
    path.write_text(
        "huri_url: ws://127.0.0.1:1\nuser_id_file_path: "
        f"{tmp_path / 'uid'}\nmodules:\n  shout:\n    name: shout\n{log}"
    )
    return path


def _handshake_of_yaml_client(tmp_path, log=""):
    config = load_client_config(str(_client_yaml(tmp_path, log)))

    async def scenario():
        async with FakeHuRI() as server:
            client = HuRIClient.from_config(config)
            client.url = server.url
            async with client:
                pass
            return server.handshake

    return run(scenario())


def test_the_log_section_of_a_client_yaml_reaches_the_server_in_the_handshake(
    tmp_path,
):
    log = "log:\n  modules:\n    shout: WARNING\n  topics:\n    toy.word: INFO\n"
    assert _handshake_of_yaml_client(tmp_path, log)["log"] == {
        "modules": {"shout": "WARNING"},
        "topics": {"toy.word": "INFO"},
    }


def test_a_client_without_a_log_section_sends_the_same_handshake_as_before(tmp_path):
    assert "log" not in _handshake_of_yaml_client(tmp_path)


def test_the_client_logs_each_frame_it_sends_and_receives_at_debug(caplog):
    caplog.set_level(logging.DEBUG, logger="huri.client")

    async def scenario():
        async with FakeHuRI(outgoing=[encode("toy.token", Text("ok"))]) as server:
            client = HuRIClient(server.url, modules={}, sends=["toy.question"])
            got = []
            client.on("toy.token", Text, lambda e: _append(got, e))
            async with client:
                await client.send("toy.question", Text("hi"))
                await until(lambda: got)

    run(scenario())
    lines = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert any(
        lvl == logging.DEBUG and m.startswith("sent 'toy.question'") for lvl, m in lines
    )
    assert any(
        lvl == logging.DEBUG and m.startswith("received 'toy.token'")
        for lvl, m in lines
    )


def test_a_topic_set_to_info_has_no_traffic_lines_in_the_client_log(caplog):
    caplog.set_level(logging.DEBUG, logger="huri.client")

    async def scenario():
        async with FakeHuRI(outgoing=[encode("toy.token", Text("ok"))]) as server:
            client = HuRIClient(
                server.url, modules={}, log={"topics": {"toy.token": "INFO"}}
            )
            got = []
            client.on("toy.token", Text, lambda e: _append(got, e))
            async with client:
                await until(lambda: got)
            return got

    assert run(scenario()) == [Text("ok")]
    assert "received 'toy.token'" not in caplog.text


def test_a_sends_topic_set_to_info_has_no_sent_line_in_the_client_log(caplog):
    caplog.set_level(logging.DEBUG, logger="huri.client")

    async def scenario():
        async with FakeHuRI() as server:
            client = HuRIClient(
                server.url,
                modules={},
                sends=["toy.question"],
                log={"topics": {"toy.question": "INFO"}},
            )
            async with client:
                await client.send("toy.question", Text("hi"))
                await until(lambda: server.received)

    run(scenario())
    assert "sent 'toy.question'" not in caplog.text


def test_a_client_logger_above_debug_never_summarizes_frames(caplog):
    caplog.set_level(logging.INFO, logger="huri.client")
    calls = []

    @dataclass
    class Spy(Text):
        def summarize(self):
            calls.append(1)
            return "spy"

    async def scenario():
        async with FakeHuRI(outgoing=[encode("toy.token", Text("ok"))]) as server:
            client = HuRIClient(server.url, modules={}, sends=["toy.question"])
            got = []
            client.on("toy.token", Spy, lambda e: _append(got, e))
            async with client:
                await client.send("toy.question", Spy("hi"))
                await until(lambda: got)

    run(scenario())
    assert calls == []


def test_a_client_given_an_unknown_log_level_fails_before_connecting():
    with pytest.raises(ValueError, match="LOUD"):
        HuRIClient(
            "ws://127.0.0.1:1", modules={}, log={"topics": {"toy.token": "LOUD"}}
        )
