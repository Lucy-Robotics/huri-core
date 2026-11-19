"""What this protects: the event bus's per-publish cost stays visible. Numbers
are per batch of 1000 publishes (or per 5 chained events), subscribers' tasks
included. A sink that misses an event fails the test instead of timing it."""

import asyncio
from dataclasses import dataclass

import pytest

from huri_core.core.bus import EventGraph
from huri_core.core.events import BytesEvent, EventData
from huri_core.core.module import Module

PUBLISHES = 1000
TIMEOUT = 5  # a sink that misses an event fails here instead of hanging
CHUNK = BytesEvent(b"\0" * 640)


@dataclass
class Text(EventData):
    value: str


class CountingSink(Module):
    output_type = None

    def __init__(self, topic: str, expected: int):
        self.input_type = topic
        self.expected = expected
        self.count = 0
        self.done = asyncio.Event()

    async def process(self, data):
        self.count += 1
        if self.count % self.expected == 0:
            self.done.set()


class Streamer(Module):
    input_type = "huri.text"
    output_type = "huri.token"

    async def process(self, data):
        for i in range(5):
            yield Text(str(i))


@pytest.mark.parametrize("subscribers", [1, 10])
def test_the_event_bus_cost_is_measured_per_publish(benchmark, loop, subscribers):
    graph = EventGraph()
    sinks = [CountingSink("huri.audio.in", PUBLISHES) for _ in range(subscribers)]
    for sink in sinks:
        graph.register(sink)

    async def batch():
        for sink in sinks:
            sink.done.clear()
        for _ in range(PUBLISHES):
            await graph.publish("huri.audio.in", CHUNK)
        for sink in sinks:
            await asyncio.wait_for(sink.done.wait(), TIMEOUT)

    benchmark(lambda: loop.run_until_complete(batch()))

    rounds = benchmark.stats.stats.rounds if benchmark.stats else 1
    for sink in sinks:
        assert sink.count >= PUBLISHES * rounds
        assert sink.count % PUBLISHES == 0


def test_a_streaming_modules_chained_events_are_measured_end_to_end(benchmark, loop):
    graph = EventGraph()
    sink = CountingSink("huri.token", 5)
    graph.register(Streamer())
    graph.register(sink)

    async def chain():
        sink.done.clear()
        await graph.publish("huri.text", Text("go"))
        await asyncio.wait_for(sink.done.wait(), TIMEOUT)

    benchmark(lambda: loop.run_until_complete(chain()))

    rounds = benchmark.stats.stats.rounds if benchmark.stats else 1
    assert sink.count >= 5 * rounds
    assert sink.count % 5 == 0
