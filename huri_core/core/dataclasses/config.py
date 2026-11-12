import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Hashable, Iterable, List, Mapping, Optional

_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


@dataclass
class ModuleConfig:
    """A module of the session's pipeline, given either by registered `name`
    (an installed `huri.modules` entry point) or by `module` import path
    (`my_pkg.servo:Servo`, which the server must list in HURI_MODULES)."""

    name: Optional[str]
    args: Mapping[str, Any]
    module: Optional[str] = None

    @property
    def key(self) -> str:
        """What the server's module registry is looked up with."""
        return self.module or self.name or ""

    @classmethod
    def from_dict(self, raw: dict) -> "ModuleConfig":
        name, module = raw.get("name"), raw.get("module")
        if (name is None) == (module is None):
            raise ValueError(
                f"a module entry needs exactly one of 'name' or 'module': {raw!r}"
            )
        return self(name=name, args=raw.get("args", {}), module=module)

    def to_dict(self) -> Dict[str, Any]:
        raw: Dict[str, Any] = {"args": dict(self.args)}
        if self.module is not None:
            raw["module"] = self.module
        else:
            raw["name"] = self.name
        return raw


@dataclass
class LogConfig:
    """A session's traffic log levels (the `log:` section): an event line is
    logged when its level reaches both its topic's and its module's level.

    :modules: module id (a key of the session's `modules:`) -> level.
    :topics: topic -> level.
    """

    modules: Dict[Hashable, int] = field(default_factory=dict)
    topics: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, Any], module_ids: Iterable[Hashable]
    ) -> "LogConfig":
        ids = list(module_ids)
        if not isinstance(raw, Mapping):
            raise ValueError(f"`log` must be a mapping, not {raw!r}")
        # Other keys under `log:` are ignored, like ClientConfig's top level:
        # a newer client's log options must not break an older server.
        sections = {}
        for section in ("modules", "topics"):
            levels = raw.get(section)
            levels = {} if levels is None else levels
            if not isinstance(levels, Mapping):
                raise ValueError(f"`log.{section}` must be a mapping, not {levels!r}")
            sections[section] = {k: cls._level(v) for k, v in levels.items()}
        unknown = [k for k in sections["modules"] if k not in ids]
        if unknown:
            raise ValueError(
                f"`log.modules` sets {unknown}, which are not module ids of this "
                f"session: {ids}"
            )
        return cls(**sections)

    @staticmethod
    def _level(name: Any) -> int:
        level = _LOG_LEVELS.get(str(name).upper())
        if level is None:
            raise ValueError(
                f"unknown log level {name!r}: use one of {list(_LOG_LEVELS)}"
            )
        return level

    def to_dict(self) -> Dict[str, Dict[Hashable, str]]:
        sections: Dict[str, Mapping[Any, int]] = {
            "modules": self.modules,
            "topics": self.topics,
        }
        return {
            section: {k: logging.getLevelName(v) for k, v in levels.items()}
            for section, levels in sections.items()
            if levels
        }

    def allows(
        self, level: int, topic: str, module_id: Optional[Hashable] = None
    ) -> bool:
        return level >= max(
            self.topics.get(topic, logging.NOTSET),
            self.modules.get(module_id, logging.NOTSET),
        )


@dataclass
class ClientConfig:
    """A client session, as written in a client YAML and sent to the server as
    the session handshake.

    :modules: the session's pipeline.
    :subscribe: topics the client receives.
    :send: topics the client publishes (module inputs it provides).
    :log: the session's traffic log levels, per module id and per topic.
    :event_policy: the keyword arguments of the server's event policy for this
        session, e.g. {"interrupts": {"acme.stop": ["huri.token"]}}.
    """

    user_id: str
    modules: Dict[str, ModuleConfig]
    subscribe: List[str]
    send: List[str]
    huri_url: str = ""
    log: LogConfig = field(default_factory=LogConfig)
    event_policy: Dict[str, Any] = field(default_factory=dict)

    LEGACY_KEYS = ("interface_path", "senders", "hooks")

    @classmethod
    def from_dict(cls, raw: Dict) -> "ClientConfig":
        legacy = [k for k in cls.LEGACY_KEYS if k in raw]
        if legacy:
            raise ValueError(
                f"client config uses the removed keys {legacy}: list the topics to "
                "receive under `subscribe:` and the topics sent under `send:` "
                'instead (see "Client SDK" in the huri-core README)'
            )
        event_policy = {} if raw.get("event_policy") is None else raw["event_policy"]
        if not isinstance(event_policy, Mapping):
            raise ValueError(f"`event_policy` must be a mapping, not {event_policy!r}")
        modules = {
            module_id: ModuleConfig.from_dict(mod_raw)
            for module_id, mod_raw in raw.get("modules", {}).items()
        }
        return cls(
            user_id=raw["user_id"],
            huri_url=raw.get("huri_url", ""),
            modules=modules,
            subscribe=list(raw.get("subscribe", [])),
            send=list(raw.get("send", [])),
            log=LogConfig.from_dict(
                {} if raw.get("log") is None else raw["log"], modules
            ),
            event_policy=dict(event_policy),
        )
