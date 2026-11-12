import asyncio
import contextlib
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, Sequence

import pytest

from huri_core.core.bus import EventGraph
from huri_core.core.dataclasses.config import LogConfig
from huri_core.core.events import EventData
from huri_core.core.module import Module
from huri_core.core.policy import EventPolicy, InterruptPolicy
from huri_core.core.session import Session

STOP_RULES = {"acme.stop": ["huri.token", "huri.audio.out"]}

# ---------------------------------------------------------------------------
# Helpers: toy modules driven by asyncio primitives, never by sleeps
# ---------------------------------------------------------------------------


@dataclass
class Text(EventData):
    value: str


class Streamer(Module):
    """Streams `parts` items per input ("<input>/0", "<input>/1"...). For an
    input listed in `hold`, it waits for that input's gate after each item."""

    def __init__(
        self,
        input_type: str,
        output_type: str,
        parts: int = 2,
        hold: Sequence[str] = (),
        lock: Optional[asyncio.Lock] = None,
    ):
        self.input_type = input_type
        self.output_type = output_type
        self.parts = parts
        self.gates = {value: asyncio.Event() for value in hold}
        self.lock = lock
        self.finished = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def process(self, data):
        gate = self.gates.get(data.value)
        try:
            async with self.lock or contextlib.nullcontext():
                for i in range(self.parts):
                    yield Text(f"{data.value}/{i}")
                    if gate is not None:
                        await gate.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        self.finished.set()


class Sink(Module):
    """Records what arrives on its topic, with one Event per received item."""

    output_type = None

    def __init__(self, topic: str):
        self.input_type = topic
        self.items: list[str] = []
        self.got: defaultdict[str, asyncio.Event] = defaultdict(asyncio.Event)

    async def process(self, data):
        self.items.append(data.value)
        self.got[data.value].set()


async def within(awaitable):
    await asyncio.wait_for(awaitable, timeout=1)


async def settled(graph: EventGraph) -> None:
    """Wait until the graph runs nothing, including the work its runs start;
    times out if a finished run is never forgotten."""

    async def drain():
        while graph._runs:
            await asyncio.wait(list(graph._runs))
            await asyncio.sleep(0)  # a yield, so the done callbacks can run

    await within(drain())


def graph_of(policy: Optional[EventPolicy], *modules: Module) -> EventGraph:
    graph = EventGraph() if policy is None else EventGraph(policy=policy)
    for module in modules:
        graph.register(module)
    return graph


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy",
    [None, InterruptPolicy()],
    ids=["no policy", "interrupt policy without rules"],
)
async def test_default_policy_keeps_todays_behavior(policy):
    started = [asyncio.Event(), asyncio.Event()]
    received: list[str] = []
    finished = asyncio.Event()

    class Listener(Module):
        input_type = "huri.transcript"
        output_type = None

        def __init__(self, me: asyncio.Event, other: asyncio.Event):
            self.me, self.other = me, other

        async def process(self, data):
            self.me.set()
            await self.other.wait()  # only returns if both run at once
            received.append(data.value)
            if len(received) == 2:
                finished.set()

    graph = graph_of(
        policy, Listener(started[0], started[1]), Listener(started[1], started[0])
    )

    await graph.publish("huri.transcript", Text("hello"))

    await within(finished.wait())
    assert received == ["hello", "hello"]


@pytest.mark.asyncio
async def test_urgent_event_cuts_short_a_running_less_urgent_stream():
    llm = Streamer("huri.question", "huri.token", hold=["What is HuRI?"])
    tokens = Sink("huri.token")
    graph = graph_of(InterruptPolicy(STOP_RULES), llm, tokens)
    await graph.publish("huri.question", Text("What is HuRI?"))
    await within(tokens.got["What is HuRI?/0"].wait())

    await graph.publish("acme.stop", Text("stop"))

    await within(llm.cancelled.wait())
    llm.gates["What is HuRI?"].set()  # a live stream would now emit "/1"
    await settled(graph)
    assert tokens.items == ["What is HuRI?/0"]


@pytest.mark.asyncio
async def test_interruption_also_stops_the_work_the_stream_already_started():
    llm = Streamer("huri.question", "huri.token", parts=1)
    tts = Streamer("huri.token", "huri.audio.out", hold=["What is HuRI?/0"])
    audio = Sink("huri.audio.out")
    # Only the stream is named: the TTS is stopped because the stream started it.
    graph = graph_of(InterruptPolicy({"acme.stop": ["huri.token"]}), llm, tts, audio)
    await graph.publish("huri.question", Text("What is HuRI?"))
    await within(llm.finished.wait())
    await within(audio.got["What is HuRI?/0/0"].wait())

    await graph.publish("acme.stop", Text("stop"))

    await within(tts.cancelled.wait())
    tts.gates["What is HuRI?/0"].set()  # a live TTS would now emit "/0/1"
    await settled(graph)
    assert audio.items == ["What is HuRI?/0/0"]


@pytest.mark.asyncio
async def test_a_new_question_does_not_wait_behind_the_answer_it_interrupts():
    # One question at a time, like RAG's _answer_lock.
    llm = Streamer(
        "huri.question", "huri.token", hold=["What is HuRI?"], lock=asyncio.Lock()
    )
    tokens = Sink("huri.token")
    graph = graph_of(InterruptPolicy({"huri.question": ["huri.token"]}), llm, tokens)
    await graph.publish("huri.question", Text("What is HuRI?"))
    await within(tokens.got["What is HuRI?/0"].wait())

    await graph.publish("huri.question", Text("Can you stop and tell me the time?"))

    await within(tokens.got["Can you stop and tell me the time?/1"].wait())
    assert llm.cancelled.is_set()
    assert "What is HuRI?/1" not in tokens.items


@pytest.mark.asyncio
async def test_an_interruption_only_cuts_the_work_running_when_it_arrives():
    llm = Streamer("huri.question", "huri.token", hold=["Tell me a story"])
    tokens = Sink("huri.token")
    graph = graph_of(InterruptPolicy(STOP_RULES), llm, tokens)
    await graph.publish("huri.question", Text("Tell me a story"))
    await within(tokens.got["Tell me a story/0"].wait())
    await graph.publish("acme.stop", Text("stop"))
    await within(llm.cancelled.wait())

    await graph.publish("huri.question", Text("What is HuRI?"))

    await within(tokens.got["What is HuRI?/1"].wait())
    assert tokens.items[-2:] == ["What is HuRI?/0", "What is HuRI?/1"]


@pytest.mark.asyncio
async def test_work_outside_the_interrupted_streams_keeps_running():
    stt = Streamer("huri.audio.in", "huri.transcript", hold=["speech"])
    llm = Streamer("huri.question", "huri.token", hold=["What is HuRI?"])
    transcripts, tokens = Sink("huri.transcript"), Sink("huri.token")
    graph = graph_of(InterruptPolicy(STOP_RULES), stt, llm, transcripts, tokens)
    await graph.publish("huri.audio.in", Text("speech"))
    await graph.publish("huri.question", Text("What is HuRI?"))
    await within(transcripts.got["speech/0"].wait())
    await within(tokens.got["What is HuRI?/0"].wait())

    await graph.publish("acme.stop", Text("stop"))
    await within(llm.cancelled.wait())
    stt.gates["speech"].set()

    await within(transcripts.got["speech/1"].wait())
    assert not stt.cancelled.is_set()


@pytest.mark.asyncio
async def test_a_failing_module_does_not_block_the_bus(caplog):
    class BrokenStop(Module):
        input_type = "acme.stop"
        output_type = None

        def process(self, data):
            raise RuntimeError("boom")

    stops = Sink("acme.stop")
    graph = graph_of(InterruptPolicy(STOP_RULES), BrokenStop(), stops)
    bus_logger = logging.getLogger("ray.serve")
    bus_logger.addHandler(caplog.handler)  # Ray may stop it propagating to root
    try:
        await graph.publish("acme.stop", Text("first"))
        await within(stops.got["first"].wait())

        await graph.publish("acme.stop", Text("second"))

        await within(stops.got["second"].wait())
    finally:
        bus_logger.removeHandler(caplog.handler)
    failures = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert failures and all("BrokenStop" in r.getMessage() for r in failures)


@pytest.mark.asyncio
async def test_swapping_the_policy_needs_no_module_change():
    class RecordingPolicy(EventPolicy):
        def __init__(self):
            self.topics: list[str] = []

        async def publish(self, graph, topic, data):
            self.topics.append(topic)
            await super().publish(graph, topic, data)

    policy = RecordingPolicy()
    tokens = Sink("huri.token")
    session = Session([Streamer("huri.question", "huri.token"), tokens], policy=policy)

    await session.publish("huri.question", Text("What is HuRI?"))

    await within(tokens.got["What is HuRI?/1"].wait())
    assert policy.topics == ["huri.question", "huri.token", "huri.token"]


# ---------------------------------------------------------------------------
# Narrower behaviors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_task_that_publishes_an_urgent_event_is_not_cancelled():
    # Each question it streams interrupts the questions before it, not itself.
    stt = Streamer("huri.audio.in", "huri.question", hold=["speech"])
    questions = Sink("huri.question")
    graph = graph_of(
        InterruptPolicy({"huri.question": ["huri.question"]}), stt, questions
    )
    await graph.publish("huri.audio.in", Text("speech"))
    await within(questions.got["speech/0"].wait())

    stt.gates["speech"].set()

    await within(questions.got["speech/1"].wait())
    await within(stt.finished.wait())
    assert not stt.cancelled.is_set()


def test_interrupt_rule_naming_an_unknown_topic_is_refused():
    policy = InterruptPolicy({"acme.stopp": ["huri.token"]})

    with pytest.raises(ValueError, match="acme.stopp") as error:
        policy.check({"acme.stop", "huri.token"})

    assert "'acme.stop', 'huri.token'" in str(error.value)
    InterruptPolicy(STOP_RULES).check({"acme.stop", "huri.token", "huri.audio.out"})


@pytest.mark.parametrize(
    "interrupts, culprit",
    [
        ({"acme.stop": "huri.token"}, "acme.stop"),
        ({"acme.stop": ["huri.token", 3]}, "acme.stop"),
        (["acme.stop"], "acme.stop"),
    ],
)
def test_a_malformed_interrupt_rule_is_refused(interrupts, culprit):
    with pytest.raises(ValueError, match=culprit):
        InterruptPolicy(interrupts)


class Servo(Streamer):
    """An actuation module: the bus audits it."""


@pytest.mark.asyncio
async def test_a_cancelled_actuation_module_leaves_a_cancelled_line_in_the_audit(
    graph_logs,
):
    servo = Servo("acme.motion", "acme.angles", hold=["wave"])
    llm = Streamer("huri.question", "huri.token", hold=["What is HuRI?"])
    graph = EventGraph(
        "s-1",
        "robot",
        labels={Servo: "servo (acme-servo 0.1.0)"},
        audited={Servo},
        policy=InterruptPolicy({"acme.stop": ["acme.angles", "huri.token"]}),
    )
    angles, tokens = Sink("acme.angles"), Sink("huri.token")
    for module in (servo, llm, angles, tokens):
        graph.register(module)
    await graph.publish("acme.motion", Text("wave"))
    await graph.publish("huri.question", Text("What is HuRI?"))
    await within(angles.got["wave/0"].wait())  # mid-motion
    await within(tokens.got["What is HuRI?/0"].wait())

    await graph.publish("acme.stop", Text("stop"))

    await within(servo.cancelled.wait())
    await within(llm.cancelled.wait())
    await settled(graph)
    audit = [r.getMessage() for r in graph_logs.records if r.name == "huri.audit"]
    # Only the servo is audited: the cancelled LLM stream writes no audit line.
    assert len(audit) == 3, audit
    assert " received topic='acme.motion'" in audit[0]
    assert " emitted topic='acme.angles'" in audit[1]
    assert audit[2] == (
        "session=s-1 user=robot module=servo (acme-servo 0.1.0) cancelled "
        "topic='acme.motion'"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_level, logged", [(None, True), (logging.WARNING, False)])
async def test_the_policy_line_carries_the_session_and_follows_its_log_levels(
    graph_logs, stop_level, logged
):
    llm = Streamer("huri.question", "huri.token", hold=["What is HuRI?"])
    tokens = Sink("huri.token")
    log = LogConfig(topics={} if stop_level is None else {"acme.stop": stop_level})
    graph = EventGraph("s-1", log=log, policy=InterruptPolicy(STOP_RULES))
    for module in (llm, tokens):
        graph.register(module)
    await graph.publish("huri.question", Text("What is HuRI?"))
    await within(tokens.got["What is HuRI?/0"].wait())

    await graph.publish("acme.stop", Text("stop"))

    await within(llm.cancelled.wait())  # the level only hides the line
    policy_lines = [r.getMessage() for r in graph_logs.records if "[POLICY]" in r.msg]
    assert policy_lines == (
        [
            "[POLICY] session=s-1 'acme.stop' interrupted 1 task(s) producing "
            "['huri.token', 'huri.audio.out']"
        ]
        if logged
        else []
    )
