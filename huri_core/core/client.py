"""Client SDK: a small transport object for talking to a HuRI server.

    client = HuRIClient(url, modules={"rag": {"name": "rag"}}, sends=["huri.question"])
    client.on("huri.token", Token, handle_token)
    client.on_connection(show_status)  # ConnectionMessage: connected, refused...
    async with client:
        await client.send("huri.question", RAGQuestion(...))
        await client.wait_closed()

The SDK owns the websocket, the session handshake and the wire encoding (JSON
frames, and binary frames `[u16 topic length][topic][payload]`). It knows
nothing about audio, text or gestures: the use case registers the handlers.

Handlers of one topic run one at a time, in arrival order (audio playback
needs that); different topics run concurrently. A handler registered with
`concurrent=True` opts out: it starts on each event without waiting for its own
earlier calls or for the topic's ordered handlers (close() cancels the calls
still running). A failing handler is logged and does not stop the others.

`read_server_modules(huri_url)` reads the modules a server enabled (GET
/modules) before any session, with only the standard library.
"""

import asyncio
import http.client
import json
import logging
import struct
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from ssl import SSLContext  # not `import ssl`: the `ssl=` parameter shadows it
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Literal,
    Mapping,
    Optional,
    Set,
    Type,
    TypeVar,
)
from urllib.parse import urlsplit, urlunsplit

import websockets
from omegaconf import OmegaConf

from huri_core.core.dataclasses.config import ClientConfig, LogConfig, _latest_only_list
from huri_core.core.events import EventData, _safe_summary
from huri_core.core.user_config import get_or_create_and_save_user_id

logger = logging.getLogger("huri.client")

E = TypeVar("E", bound=EventData)
Handler = Callable[[Any], Awaitable[None]]


class HuRISessionError(RuntimeError):
    """The server refused the session (unknown module, broken wiring...)."""

    def __init__(self, error: str, server_modules: Optional[List[str]] = None):
        self.error = error
        self.server_modules = server_modules or []
        super().__init__(error)


class HuRIModulesError(RuntimeError):
    """The server's modules could not be read; the message names the URL."""


ConnectionStatus = Literal["connected", "refused", "error", "closed"]


@dataclass
class ConnectionMessage:
    """A change of the session's connection, given to on_connection handlers."""

    status: ConnectionStatus
    detail: str = ""  # refusal reason, error text, or close reason
    code: Optional[int] = None  # websocket close code, for "closed"
    server_modules: List[str] = field(default_factory=list)  # for "refused"


@dataclass
class _Subscription:
    event_cls: Type[EventData]
    queue: "asyncio.Queue[Any]"
    handlers: List[Handler] = field(default_factory=list)
    concurrent: List[Handler] = field(default_factory=list)


def load_raw_client_config(path: str) -> Dict[str, Any]:
    """A client YAML, interpolations resolved, with this machine's user_id
    (persisted under `user_id_file_path`, or the user config dir) added."""

    with open(path) as f:
        resolved = OmegaConf.to_container(OmegaConf.load(f), resolve=True)
    if not isinstance(resolved, dict):
        raise ValueError(f"{path}: a client config must be a YAML mapping")
    raw: Dict[str, Any] = {str(k): v for k, v in resolved.items()}
    raw["user_id"] = get_or_create_and_save_user_id(raw.get("user_id_file_path"))
    return raw


def load_client_config(path: str) -> ClientConfig:
    return ClientConfig.from_dict(load_raw_client_config(path))


def _modules_url(huri_url: str) -> str:
    """ws -> http, wss -> https, last path segment -> `modules`; host, port and
    path prefix kept; query and userinfo (a password) dropped."""

    parts = urlsplit(huri_url)
    scheme = {"ws": "http", "wss": "https", "http": "http", "https": "https"}.get(
        parts.scheme
    )
    try:
        host, port = parts.hostname, parts.port
    except ValueError:  # non-numeric port
        host = port = None
    if scheme is None or not host:
        raise HuRIModulesError(
            f"{huri_url}: not a websocket or HTTP URL (ws, wss, http or https)"
        )
    netloc = f"[{host}]" if ":" in host else host
    if port:
        netloc += f":{port}"
    path = parts.path.rstrip("/").rsplit("/", 1)[0] + "/modules"
    return urlunsplit((scheme, netloc, path, "", ""))


def _get_modules(url: str, context: Optional[SSLContext], timeout: float) -> Any:
    try:
        with urllib.request.urlopen(url, context=context, timeout=timeout) as res:
            body = res.read()
    except urllib.error.HTTPError as e:  # before URLError, its parent
        e.close()
        raise HuRIModulesError(
            f"{url} answered {e.code} {e.reason}: is it a HuRI server?"
        ) from e
    except http.client.HTTPException as e:  # not HTTP, or cut mid-answer
        raise HuRIModulesError(f"{url} did not answer as an HTTP server ({e!r})") from e
    except TimeoutError as e:  # connected, then no answer in time
        raise HuRIModulesError(f"{url}: the server did not answer in time") from e
    except OSError as e:  # URLError (refused, connect timeout...)
        reason = getattr(e, "reason", None) or e
        raise HuRIModulesError(f"{url}: the server is unreachable ({reason})") from e
    try:
        return json.loads(body)
    except ValueError as e:
        raise HuRIModulesError(f"{url}: the answer does not list modules") from e


async def read_server_modules(
    huri_url: str, *, ssl: Optional[SSLContext] = None, timeout: float = 5.0
) -> Dict[str, Any]:
    """What a server enabled, from its GET /modules: `modules` (names),
    `details[name]` (input, output...) and `huri_core` (its version), as sent.
    `huri_url` is the session URL (ws -> http, wss -> https, last path
    segment -> `modules`); `ssl` is the TLS context for a private CA."""

    url = _modules_url(huri_url)
    try:
        # urlopen's timeout is per socket operation: this bounds the whole read.
        answer = await asyncio.wait_for(
            asyncio.to_thread(_get_modules, url, ssl, timeout), timeout
        )
    except asyncio.TimeoutError as e:
        raise HuRIModulesError(f"{url}: the server did not answer in time") from e
    if not (
        isinstance(answer, dict)
        and isinstance(answer.get("modules"), list)
        and isinstance(answer.get("details"), dict)
    ):
        raise HuRIModulesError(f"{url}: the answer does not list modules")
    return answer


def encode(topic: str, event: EventData) -> str | bytes:
    """Wire frame of an event: binary when to_wire() gives bytes, else JSON."""

    wire = event.to_wire()
    if isinstance(wire, bytes):
        topic_bytes = topic.encode()
        return struct.pack(">H", len(topic_bytes)) + topic_bytes + wire
    return json.dumps({"topic": topic, "data": wire})


def decode(message: str | bytes) -> tuple[str, Any]:
    """(topic, raw wire data) of a frame."""

    if isinstance(message, bytes):
        if len(message) < 2:
            raise ValueError("binary frame has no topic length")
        topic_len = struct.unpack(">H", message[:2])[0]
        if len(message) < 2 + topic_len:
            raise ValueError("binary frame has a truncated topic")
        return message[2 : 2 + topic_len].decode(), message[2 + topic_len :]
    frame = json.loads(message)
    if (
        not isinstance(frame, dict)
        or not isinstance(frame.get("topic"), str)
        or "data" not in frame
    ):
        raise ValueError("text frame needs a string topic and data")
    return frame["topic"], frame["data"]


class HuRIClient:
    """Connection to a HuRI server.

    :url: websocket URL of the server session endpoint.
    :modules: the session's pipeline, as in the YAML `modules:` section
        ({id: {"name": "stt", "args": {...}}} or {"module": "pkg.mod:Cls"}).
    :sends: topics this client will send; the server checks every module input
        has a producer, and send() refuses any other topic.
    :user_id: identity the server keeps per-user state under (RAG memory).
        A random one is used when omitted.
    :log: traffic log levels, as in the YAML `log:` section
        ({"modules": {id: "WARNING"}, "topics": {topic: "INFO"}}). Sent in the
        handshake for the server's logs; here, the sent/received lines the client
        writes at DEBUG on the ``huri.client`` logger skip the topics set above DEBUG.
    :event_policy: this session's event policy settings, as in the YAML
        `event_policy:` section ({"interrupts": {"acme.stop": ["huri.token"]}}).
        The server checks them and refuses the session if they are wrong.
    :latest_only: topics whose busy subscribers get only the newest value, as
        in the YAML `latest_only:` list. The server refuses the session if one
        is not a topic of the session.
    :connect_kwargs: extra options for ``websockets.connect`` (e.g. ``ssl`` for
        a server with a private CA, ``additional_headers``). By default the
        message size limit is lifted (``max_size=None``): audio and motion
        frames can exceed the library's 1 MiB default.
    """

    def __init__(
        self,
        url: str,
        modules: Mapping[str, Mapping[str, Any]],
        sends: Iterable[str] = (),
        user_id: Optional[str] = None,
        log: Optional[Mapping[str, Any]] = None,
        event_policy: Optional[Mapping[str, Any]] = None,
        latest_only: Optional[List[str]] = None,
        connect_kwargs: Optional[Mapping[str, Any]] = None,
    ):
        self.url = url
        self.connect_kwargs: Dict[str, Any] = {"max_size": None}
        self.connect_kwargs.update(connect_kwargs or {})
        self.modules = {k: dict(v) for k, v in modules.items()}
        self.sends = list(sends)
        self.user_id = user_id or str(uuid.uuid4())
        self.log = (
            LogConfig() if log is None else LogConfig.from_dict(log, self.modules)
        )
        event_policy = {} if event_policy is None else event_policy
        if not isinstance(event_policy, Mapping):
            raise ValueError(f"`event_policy` must be a mapping, not {event_policy!r}")
        self.event_policy = dict(event_policy)
        self.latest_only = _latest_only_list(latest_only)
        self._subs: Dict[str, _Subscription] = {}
        self._ws: Optional[websockets.ClientConnection] = None
        self._tasks: List[asyncio.Task] = []
        # Concurrent handler calls; each discards itself when done.
        self._handler_tasks: Set[asyncio.Task] = set()
        self._connection_handlers: List[
            Callable[[ConnectionMessage], Awaitable[None]]
        ] = []

    @classmethod
    def from_config(cls, config: ClientConfig) -> "HuRIClient":
        """A client for a session described by a client YAML (load_client_config).
        Its `subscribe` topics still need handlers, registered with on()."""

        return cls(
            config.huri_url,
            modules={k: m.to_dict() for k, m in config.modules.items()},
            sends=config.send,
            user_id=config.user_id,
            log=config.log.to_dict(),
            event_policy=config.event_policy,
            latest_only=config.latest_only,
        )

    # --- configuration ----------------------------------------------------

    def on(
        self,
        topic: str,
        event_cls: Type[E],
        handler: Callable[[E], Awaitable[None]],
        *,
        concurrent: bool = False,
    ) -> None:
        """Call `handler(event)` for every `topic` event. Before connecting:
        the subscriptions are part of the session handshake.

        By default a topic's handlers run one at a time, in arrival order.
        `concurrent=True` opts this handler out of waiting: it starts on each
        event without waiting for its own earlier calls or for the topic's
        ordered handlers, and close() cancels the calls still running. On a
        topic that also has ordered handlers it sees each event when they have
        finished the previous one. Use it for order-free work (logging, metrics).
        """

        if self._ws is not None:
            raise RuntimeError("subscribe with on() before connecting")
        sub = self._subs.get(topic)
        if sub is None:
            sub = self._subs[topic] = _Subscription(event_cls, asyncio.Queue())
        elif sub.event_cls is not event_cls:
            raise ValueError(
                f"{topic!r} is already subscribed as {sub.event_cls.__name__}, "
                f"not {event_cls.__name__}"
            )
        (sub.concurrent if concurrent else sub.handlers).append(handler)

    def on_connection(
        self, handler: Callable[[ConnectionMessage], Awaitable[None]]
    ) -> None:
        """Call `handler(message)` when the session connects, is refused, fails
        to connect or closes. Can be registered at any time."""

        self._connection_handlers.append(handler)

    def handshake(self) -> Dict[str, Any]:
        """The session request sent to the server."""

        request: Dict[str, Any] = {
            "user_id": self.user_id,
            "modules": self.modules,
            "subscribe": sorted(self._subs),
            "send": self.sends,
        }
        if log := self.log.to_dict():
            request["log"] = log
        if self.event_policy:
            request["event_policy"] = self.event_policy
        if self.latest_only:
            request["latest_only"] = self.latest_only
        return request

    # --- connection ---------------------------------------------------------

    async def connect(self) -> "HuRIClient":
        try:
            ws = await websockets.connect(self.url, **self.connect_kwargs)
        except Exception as e:
            await self._report_error(e)
            raise
        refusal: Optional[ConnectionMessage] = None
        try:
            await ws.send(json.dumps(self.handshake()))
            try:
                reply = json.loads(await ws.recv())
            except websockets.ConnectionClosed as e:
                raise HuRISessionError(
                    "the server closed the connection without a session reply"
                ) from e
            if reply.get("type") == "session_error":
                error = HuRISessionError(
                    reply.get("error", ""), reply.get("server_modules")
                )
                refusal = ConnectionMessage(
                    "refused", error.error, server_modules=error.server_modules
                )
                raise error
            if reply.get("type") != "session_init":
                raise HuRISessionError(f"unexpected first message: {reply!r}")
        except BaseException as e:
            await ws.close()
            if refusal is not None:
                await self._report(refusal)
            elif isinstance(e, Exception):
                await self._report_error(e)
            raise

        self.user_id = reply.get("user_id", self.user_id)
        self._ws = ws
        self._tasks = [asyncio.create_task(self._receive_loop(ws))]
        self._tasks += [
            asyncio.create_task(self._dispatch_loop(topic, sub))
            for topic, sub in self._subs.items()
        ]
        await self._report(ConnectionMessage("connected"))
        return self

    async def close(self) -> None:
        """Close the session and wait for the "closed" handlers to finish (a
        handler that never returns makes close() hang). Callable from any
        handler. From an event handler, that handler is cancelled at its next
        await: do not reconnect from there. From a "connected" handler, the
        handlers after it see "closed" before "connected". Concurrent handler
        calls still running are cancelled."""

        ws, self._ws = self._ws, None
        if ws is None:  # not connected, or already closed
            return
        tasks, self._tasks = self._tasks, []
        handler_tasks = list(self._handler_tasks)
        self._handler_tasks.clear()
        # Called from a handler, the current task cannot await itself.
        current = asyncio.current_task()
        receive, *dispatch = tasks  # as connect() creates them
        # Before the ws closes, so no handler sends on a closed ws and no
        # dispatch loop starts a call that close() would miss.
        for task in (*dispatch, *handler_tasks):
            if task is not current:
                task.cancel()
        await ws.close()
        # The receive loop is not cancelled: it ends with the ws and reports
        # "closed", and its handlers must not be cut off.
        await asyncio.gather(
            *(t for t in (*tasks, *handler_tasks) if t is not current),
            return_exceptions=True,
        )
        if current in dispatch or current in handler_tasks:
            # A handler closed the client: it stops at its next await, so a
            # reconnect does not get two loops on one queue.
            current.cancel()

    async def __aenter__(self) -> "HuRIClient":
        return await self.connect()

    async def __aexit__(self, *_) -> None:
        await self.close()

    async def wait_closed(self) -> None:
        """Until the server closes the session."""

        if self._ws is not None:
            await self._ws.wait_closed()

    # --- traffic -----------------------------------------------------------

    def _traces(self, topic: str) -> bool:
        # Checked before summarizing: summarize() must not run when nothing is logged.
        return logger.isEnabledFor(logging.DEBUG) and self.log.allows(
            logging.DEBUG, topic
        )

    async def send(self, topic: str, event: EventData) -> None:
        if self._ws is None:
            raise RuntimeError("not connected")
        if topic not in self.sends:
            raise ValueError(
                f"{topic!r} is not in this client's sends {self.sends}: declare it "
                "so the server can check the session wiring"
            )
        await self._ws.send(encode(topic, event))
        if self._traces(topic):
            logger.debug("sent %r: %s", topic, _safe_summary(event))

    async def _receive_loop(self, ws: websockets.ClientConnection) -> None:
        try:
            async for message in ws:
                try:
                    topic, data = decode(message)
                except (ValueError, RecursionError) as error:
                    if isinstance(message, bytes):
                        kind, size = "binary", len(message)
                    else:
                        kind, size = "text", len(message.encode("utf-8"))
                    # Exception text can include remote content; log its type only.
                    logger.warning(
                        "dropping malformed %s frame (%d bytes): %s",
                        kind,
                        size,
                        type(error).__name__,
                    )
                    continue
                sub = self._subs.get(topic)
                if sub is None:
                    logger.warning("dropping frame on unsubscribed topic %r", topic)
                    continue
                sub.queue.put_nowait(data)
        except websockets.ConnectionClosed:
            pass
        await self._report(
            ConnectionMessage("closed", ws.close_reason or "", code=ws.close_code)
        )

    async def _report_error(self, e: Exception) -> None:
        await self._report(ConnectionMessage("error", f"{type(e).__name__}: {e}"))

    async def _report(self, message: ConnectionMessage) -> None:
        for handler in list(self._connection_handlers):
            try:
                await handler(message)
            except Exception:
                logger.exception("connection handler failed on %r", message.status)

    async def _dispatch_loop(self, topic: str, sub: _Subscription) -> None:
        while True:
            data = await sub.queue.get()
            try:
                # from_wire is the decoding contract for both wire shapes.
                event = sub.event_cls.from_wire(data)
            except Exception:
                logger.exception("cannot decode %r frame as %s", topic, sub.event_cls)
                continue
            if self._traces(topic):
                logger.debug("received %r: %s", topic, _safe_summary(event))
            for handler in sub.concurrent:
                task = asyncio.create_task(self._call(topic, handler, event))
                self._handler_tasks.add(task)
                task.add_done_callback(self._handler_tasks.discard)
            for handler in sub.handlers:
                await self._call(topic, handler, event)

    async def _call(self, topic: str, handler: Handler, event: Any) -> None:
        try:
            await handler(event)
        except Exception:
            logger.exception("handler for %r failed", topic)
