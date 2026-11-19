import asyncio
import logging
import time
from collections import defaultdict
from typing import (
    Any,
    AsyncGenerator,
    Collection,
    Coroutine,
    Hashable,
    Mapping,
    Optional,
    cast,
)

from .dataclasses.config import LogConfig
from .events import EventData, _safe_summary
from .module import Module
from .policy import EventPolicy

logger = logging.getLogger("ray.serve")
audit_logger = logging.getLogger("huri.audit")

# How long cancel_all() waits for cancelled work: a module that swallows the
# cancellation must not keep the session from finalizing.
CANCEL_TIMEOUT_S = 5

# A topic's publish line is logged once per window, then counted: it is the same
# line every time, and logging it per event (microphone audio) doubled the cost
# of a publish.
PUBLISH_LOG_WINDOW_S = 1.0

# Built once: subscripting a generic in _run cost a typing call per event.
_Generator = AsyncGenerator[EventData | None, None]
_Coroutine = Coroutine[Any, Any, EventData | None]


class EventGraph:
    """
    Asynchronous event routing system for HuRI modules.

    The EventGraph is responsible for:
        - Registering module subscribers
        - Routing events between modules
        - Executing module pipelines asynchronously
        - Handling coroutine and async generator outputs

    Modules subscribe to events through their `input_type`.
    When an event is published, all subscribed modules are executed
    concurrently.

    Supports:
        - Coroutine-based modules
        - Async generator streaming modules
        - Recursive event propagation

    :subscribers:
        Dictionary mapping event topics to subscribed modules.
    :labels:
        Module class -> "name (package version)" shown in logs.
    :audited:
        Module classes (actuation) whose every input and output is audited
        on the "huri.audit" logger.
    :policy:
        Decides how each published event is dispatched (EventPolicy() by
        default: every subscriber at once).
    :latest_only:
        Topics whose subscribers only get the newest value while busy.
    """

    def __init__(
        self,
        session_id: str = "-",
        user_id: str = "-",
        labels: Optional[Mapping[type, str]] = None,
        audited: Collection[type] = (),
        log: Optional[LogConfig] = None,
        module_ids: Optional[Mapping[int, Hashable]] = None,
        policy: Optional[EventPolicy] = None,
        latest_only: Collection[str] = (),
    ) -> None:
        self._latest_only = frozenset(latest_only)
        self._log = log or LogConfig()
        self._module_ids = module_ids or {}  # id(module) -> its id in the session
        self.subscribers: defaultdict[str, list[Module]] = defaultdict(list)
        self._policy = policy if policy is not None else EventPolicy()
        self._runs: dict[asyncio.Task, _Run] = {}
        # topic -> (end of its publish-line window, publishes not logged in it)
        self._publish_lines: dict[str, tuple[float, int]] = {}
        # Latest-only slots, keyed (id(module), topic): id, as modules may define __eq__
        # shortcut: one module object registered twice on a topic shares one slot,
        # key on the registration instead if a pipeline ever registers a module twice
        self._busy: set[tuple[int, str]] = set()
        self._pending: dict[tuple[int, str], tuple[Module, Any, _Run | None]] = {}
        self._session_id = session_id
        self._user_id = user_id
        self._labels = labels or {}
        self._audited = audited

    def _label(self, module: Module) -> str:
        return self._labels.get(type(module), type(module).__name__)

    def register(self, module: Module):
        self.subscribers[module.input_type].append(module)

    async def publish(self, event_topic, data):
        if logger.isEnabledFor(logging.INFO) and self._log.allows(
            logging.INFO, event_topic
        ):
            self._log_publish(event_topic)
        await self._policy.publish(self, event_topic, data)

    def _log_publish(self, topic: str) -> None:
        now = time.monotonic()
        until, held = self._publish_lines.get(topic, (0.0, 0))
        if now < until:
            self._publish_lines[topic] = (until, held + 1)
            return
        self._publish_lines[topic] = (now + PUBLISH_LOG_WINDOW_S, 0)
        logger.info(
            "[GRAPH] session=%s publish topic=%r subscribers=%s%s",
            self._session_id,
            topic,
            [self._label(m) for m in self.subscribers[topic]],
            f" (+{held} earlier, not logged)" if held else "",
        )

    def dispatch(self, topic, data):
        """Start every subscriber of `topic`, as children of the current run."""
        current = asyncio.current_task()
        parent = self._runs.get(current) if current else None
        if topic in self._latest_only:
            for module in self.subscribers[topic]:
                self._dispatch_latest(module, topic, data, parent)
            return
        for module in self.subscribers[topic]:
            self._start(module, data, parent)

    def _start(self, module: Module, data, parent: "_Run | None") -> asyncio.Task:
        task = asyncio.create_task(self._run(module, data))
        # shortcut: the lineage chain grows one node per hop if a pipeline loops
        # on its own topics (and a rule then reaches every earlier producer's
        # descendants), add a depth bound when a looping pipeline exists
        self._runs[task] = _Run(module, parent)
        task.add_done_callback(self._forget)
        return task

    def _dispatch_latest(self, module: Module, topic: str, data, parent: "_Run | None"):
        """Start `module` on `data`, or, while it is busy on `topic`, keep
        `data` as the one value it gets next (replacing an older one)."""
        key = (id(module), topic)
        if key in self._busy:
            self._pending[key] = (module, data, parent)
            return
        task = self._start(module, data, parent)
        self._busy.add(key)
        task.add_done_callback(lambda _: self._next_latest(key, topic))

    def _next_latest(self, key: tuple[int, str], topic: str):
        self._busy.discard(key)
        if key in self._pending:
            module, data, parent = self._pending.pop(key)
            self._dispatch_latest(module, topic, data, parent)

    def cancel(self, topics) -> int:
        """
        Cancel every running task that produces one of `topics`, or that such
        a task started, except the current task. Returns how many. Also drops
        the waiting latest-only values whose run would be cancelled that way
        (not counted).
        """
        current = asyncio.current_task()
        cancelled = 0
        for task, run in self._runs.items():
            if _produces(run, topics) and task is not current and task.cancel():
                cancelled += 1
        for key, (module, _, parent) in list(self._pending.items()):
            if _produces(_Run(module, parent), topics):
                del self._pending[key]
        return cancelled

    async def cancel_all(self) -> int:
        """Cancel every running task, except the current one, and wait for them
        to stop, so finalize() sees stopped work. Returns how many. A task that
        ignores the cancellation is logged and left, never waited on forever.
        Work started while waiting (a module publishing as it is cancelled, a
        waiting latest-only value) is cancelled too, within the same deadline."""
        current = asyncio.current_task()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + CANCEL_TIMEOUT_S
        seen: set[asyncio.Task] = set()
        stuck: set[asyncio.Task] = set()
        cancelled = 0
        while True:
            # A waiting latest-only value would start when its busy run ends.
            self._pending.clear()
            tasks = {t for t in self._runs if t is not current}
            if not tasks:
                break
            cancelled += sum(t.cancel() for t in tasks - seen)
            seen |= tasks
            remaining = deadline - loop.time()
            if remaining <= 0:
                stuck = tasks
                break
            await asyncio.wait(tasks, timeout=remaining)
        for task in stuck:
            logger.warning(
                "[GRAPH] session=%s %s ignored cancellation for %ss",
                self._session_id,
                self._label(self._runs[task].module),
                CANCEL_TIMEOUT_S,
            )
        return cancelled

    def _forget(self, task: asyncio.Task):
        self._runs.pop(task, None)

    async def _emit(self, module: Module, item: EventData) -> None:
        logged = logger.isEnabledFor(logging.INFO) and self._log.allows(
            logging.INFO, module.output_type or "", self._module_ids.get(id(module))
        )
        # The audit trail ignores the client's log levels: a client can't hide it.
        audited = type(module) in self._audited
        if logged or audited:
            label = self._label(module)
            summary = _safe_summary(item)
            if logged:
                logger.info(
                    "[GRAPH] session=%s %s -> %r: %s",
                    self._session_id,
                    label,
                    module.output_type,
                    summary,
                )
            if audited:
                audit_logger.info(
                    "session=%s user=%s module=%s emitted topic=%r event=%s",
                    self._session_id,
                    self._user_id,
                    label,
                    module.output_type,
                    summary,
                )
        # No await before publish: an emitted line means dispatched, and a bus
        # cancel lands only inside the module's own await.
        await self.publish(module.output_type, item)

    async def _run(self, module: Module, data):
        try:
            if type(module) in self._audited:
                audit_logger.info(
                    "session=%s user=%s module=%s received topic=%r event=%s",
                    self._session_id,
                    self._user_id,
                    self._label(module),
                    module.input_type,
                    _safe_summary(data),
                )
            result = module.process(data)

            if hasattr(result, "__aiter__"):
                try:
                    generator = cast(_Generator, result)
                    async for item in generator:
                        if item is None:
                            continue
                        await self._emit(module, item)
                except Exception:
                    logger.exception(
                        "[GRAPH] session=%s async generator failed in %s",
                        self._session_id,
                        self._label(module),
                    )

            else:
                coroutine = cast(_Coroutine, result)
                try:
                    value = await coroutine
                    if value is not None:
                        await self._emit(module, value)
                except Exception:
                    logger.exception(
                        "[GRAPH] session=%s coroutine failed in %s",
                        self._session_id,
                        self._label(module),
                    )

        except asyncio.CancelledError:
            # Else a cancelled actuation run reads like one that completed silently.
            if type(module) in self._audited:
                audit_logger.info(
                    "session=%s user=%s module=%s cancelled topic=%r",
                    self._session_id,
                    self._user_id,
                    self._label(module),
                    module.input_type,
                )
            raise
        except Exception:
            logger.exception(
                "[GRAPH] session=%s process() call failed in %s",
                self._session_id,
                self._label(module),
            )


def _produces(run: "_Run | None", topics) -> bool:
    """Whether `run`, or a run up its lineage, produces one of `topics`."""
    while run is not None and run.module.output_type not in topics:
        run = run.parent
    return run is not None


class _Run:
    """A task the bus started: its module, and the run that published its input."""

    __slots__ = ("module", "parent")

    def __init__(self, module: Module, parent: "_Run | None"):
        self.module = module
        self.parent = parent
