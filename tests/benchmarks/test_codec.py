"""What this protects: the client wire codec's round-trip cost per frame stays
visible, and the frame still decodes to the event it was made from."""

from dataclasses import dataclass

import pytest

pytest.importorskip("websockets", reason="the wire codec needs the client extra")

from huri_core.core.client import decode, encode  # noqa: E402
from huri_core.core.events import BytesEvent, JsonEvent  # noqa: E402


@dataclass
class Transcript(JsonEvent):
    text: str


FRAMES = {
    "json": ("huri.transcript", Transcript("x" * 200)),
    "binary": ("huri.audio.in", BytesEvent(bytes(32 * 1024))),
}


@pytest.mark.parametrize("kind", FRAMES)
def test_the_wire_codec_cost_is_measured_per_frame(benchmark, kind):
    topic, event = FRAMES[kind]

    result = benchmark(lambda: decode(encode(topic, event)))

    assert result == (topic, event.to_wire())
