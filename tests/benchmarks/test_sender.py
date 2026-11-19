"""What this protects: the server Sender's cost per batch of 1000 frames stays
visible, and every frame still reaches the socket."""

from dataclasses import dataclass

import pytest

pytest.importorskip("fastapi", reason="the Sender needs the server extra")

from huri_core.core.events import BytesEvent, JsonEvent  # noqa: E402
from huri_core.core.sender import Sender  # noqa: E402


@dataclass
class Transcript(JsonEvent):
    text: str


class CountingSocket:
    def __init__(self):
        self.sent = 0

    async def send_json(self, message):
        self.sent += 1

    async def send_bytes(self, payload):
        self.sent += 1


FRAMES_PER_ROUND = 1000  # one loop run per frame would mostly time the loop
FRAMES = {
    "json": ("huri.transcript", Transcript("x" * 200)),
    "binary": ("huri.audio.in", BytesEvent(bytes(32 * 1024))),
}


@pytest.mark.parametrize("kind", FRAMES)
def test_the_servers_sender_cost_is_measured_per_frame(benchmark, loop, kind):
    topic, event = FRAMES[kind]
    ws = CountingSocket()
    sender = Sender(ws, topic)

    async def frames():
        for _ in range(FRAMES_PER_ROUND):
            await sender.process(event)

    benchmark(lambda: loop.run_until_complete(frames()))

    rounds = benchmark.stats.stats.rounds if benchmark.stats else 1
    assert ws.sent >= FRAMES_PER_ROUND * rounds
