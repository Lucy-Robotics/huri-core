"""The client SDK against a local websocket server standing in for HuRI."""

import asyncio
import json
import logging
from dataclasses import dataclass

import pytest
import websockets

from huri_core.core.client import HuRIClient, HuRISessionError, decode, encode
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
