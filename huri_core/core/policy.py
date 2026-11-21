import logging
from typing import TYPE_CHECKING, Collection, Mapping, Optional, Sequence

if TYPE_CHECKING:
    from .bus import EventGraph

logger = logging.getLogger("ray.serve")


class EventPolicy:
    """
    How a session's EventGraph hands out each published event.

    The base policy starts every subscriber of the topic at once, as the bus
    always did. A subclass can cancel, delay or reorder work around
    `graph.dispatch`, without any module change.

    shortcut: lineage only follows tasks the bus started, so the work a module
    starts in its own task is not cancelled with it; track those tasks too if
    a module ever publishes from one.
    """

    async def publish(self, graph: "EventGraph", topic: str, data) -> None:
        graph.dispatch(topic, data)

    def check(self, topics: Collection[str]) -> None:
        """Raise ValueError if the policy names a topic outside `topics`."""


class InterruptPolicy(EventPolicy):
    """
    An event on a rule's topic first cancels the running work that produces
    one of the rule's topics, and what that work started, then is dispatched.

    :interrupts:
        Topic -> the topics it interrupts, e.g.
        {"acme.stop": ["huri.token", "huri.audio.out"]}. None: no rule, which
        is the base policy's behavior.
    """

    def __init__(self, interrupts: Optional[Mapping[str, Sequence[str]]] = None):
        interrupts = {} if interrupts is None else interrupts
        if not isinstance(interrupts, Mapping):
            raise ValueError(
                f"interrupts {interrupts!r} must map a topic to the topics it "
                "interrupts"
            )
        for topic, targets in interrupts.items():
            if (
                not isinstance(topic, str)
                or isinstance(targets, str)
                or not isinstance(targets, Sequence)
                or not all(isinstance(target, str) for target in targets)
            ):
                raise ValueError(
                    f"interrupt rule {topic!r}: {targets!r} must map a topic to "
                    "a list of topics"
                )
        self._interrupts = {
            topic: tuple(targets) for topic, targets in interrupts.items()
        }

    def check(self, topics: Collection[str]) -> None:
        named = {
            t for topic, targets in self._interrupts.items() for t in (topic, *targets)
        }
        unknown = sorted(named - set(topics))
        if unknown:
            raise ValueError(
                f"interrupt rules name unknown topics {unknown}; "
                f"this session's topics are {sorted(topics)}"
            )

    async def publish(self, graph: "EventGraph", topic: str, data) -> None:
        targets = self._interrupts.get(topic, ())
        # Cancel before dispatching, so a rule may interrupt its own topic.
        cancelled = graph.cancel(targets) if targets else 0
        if cancelled and graph._log.allows(logging.INFO, topic):
            logger.info(
                "[POLICY] session=%s %r interrupted %d task(s) producing %s",
                graph._session_id,
                topic,
                cancelled,
                list(targets),
            )
        graph.dispatch(topic, data)
