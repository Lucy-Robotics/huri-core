from typing import Collection, Hashable, List, Mapping, Optional

from .bus import EventGraph
from .dataclasses.config import LogConfig
from .module import Module
from .policy import EventPolicy


class Session:
    """
    Realtime conversational session container.

    A Session represents an isolated runtime pipeline for a single
    connected client. It manages event routing between registered
    modules through an internal EventGraph.

    Sessions are typically created per WebSocket connection and
    destroyed when the client disconnects.

    :event_graph:
        Internal asynchronous event routing system.
    """

    def __init__(
        self,
        modules: List[Module],
        session_id: str = "-",
        user_id: str = "-",
        labels: Optional[Mapping[type, str]] = None,
        audited: Collection[type] = (),
        log: Optional[LogConfig] = None,
        module_ids: Optional[Mapping[int, Hashable]] = None,
        policy: Optional[EventPolicy] = None,
    ) -> None:
        self.event_graph = EventGraph(
            session_id,
            user_id,
            labels,
            audited,
            log=log,
            module_ids=module_ids,
            policy=policy,
        )

        for module in modules:
            self.event_graph.register(module)

    async def publish(self, topic, data):
        await self.event_graph.publish(topic, data)
