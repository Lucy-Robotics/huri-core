import asyncio
import logging
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

logger = logging.getLogger("ray.serve")
audit_logger = logging.getLogger("huri.audit")


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
    """

    def __init__(
        self,
        session_id: str = "-",
        user_id: str = "-",
        labels: Optional[Mapping[type, str]] = None,
        audited: Collection[type] = (),
        log: Optional[LogConfig] = None,
        module_ids: Optional[Mapping[int, Hashable]] = None,
    ) -> None:
        self._log = log or LogConfig()
        self._module_ids = module_ids or {}  # id(module) -> its id in the session
        self.subscribers: defaultdict[str, list[Module]] = defaultdict(list)
        self._tasks: set[asyncio.Task] = set()
        self._session_id = session_id
        self._user_id = user_id
        self._labels = labels or {}
        self._audited = audited

    def _label(self, module: Module) -> str:
        return self._labels.get(type(module), type(module).__name__)

    def register(self, module: Module):
        self.subscribers[module.input_type].append(module)

    async def publish(self, event_topic, data):
        subs = self.subscribers[event_topic]

        if logger.isEnabledFor(logging.INFO) and self._log.allows(
            logging.INFO, event_topic
        ):
            logger.info(
                "[GRAPH] session=%s publish topic=%r subscribers=%s",
                self._session_id,
                event_topic,
                [self._label(m) for m in subs],
            )
        for module in subs:
            task = asyncio.create_task(self._run(module, data))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

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
        await self.publish(module.output_type, item)

    async def _run(self, module: Module, data):
        label = self._label(module)
        try:
            if type(module) in self._audited:
                audit_logger.info(
                    "session=%s user=%s module=%s received topic=%r event=%s",
                    self._session_id,
                    self._user_id,
                    label,
                    module.input_type,
                    _safe_summary(data),
                )
            result = module.process(data)

            if hasattr(result, "__aiter__"):
                try:
                    generator = cast(AsyncGenerator[EventData | None, None], result)
                    async for item in generator:
                        if item is None:
                            continue
                        await self._emit(module, item)
                except Exception:
                    logger.exception(
                        "[GRAPH] session=%s async generator failed in %s",
                        self._session_id,
                        label,
                    )

            else:
                coroutine = cast(Coroutine[Any, Any, EventData | None], result)
                try:
                    value = await coroutine
                    if value is not None:
                        await self._emit(module, value)
                except Exception:
                    logger.exception(
                        "[GRAPH] session=%s coroutine failed in %s",
                        self._session_id,
                        label,
                    )

        except Exception:
            logger.exception(
                "[GRAPH] session=%s process() call failed in %s",
                self._session_id,
                label,
            )
