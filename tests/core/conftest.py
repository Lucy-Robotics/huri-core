"""Without huri-core[server] (Ray Serve, FastAPI), the tests of the server
parts are not collected: CI's client job runs everything else with only
huri-core[client] installed, which is what proves those parts never need the
server stack."""

import importlib.util
import logging

import pytest

SERVER_TESTS = [
    "test_plugins.py",
    "test_provenance.py",
    "test_public_api.py",  # huri_core.app, in the record, imports Ray
    "test_server_session.py",
    "test_topic_wiring.py",
]

if any(importlib.util.find_spec(m) is None for m in ("ray", "fastapi")):
    collect_ignore = SERVER_TESTS


@pytest.fixture
def graph_logs(caplog, monkeypatch):
    """caplog that sees the bus and audit loggers even once ray.serve is
    imported (it sets propagate=False); never imports ray itself."""
    # ray.serve and its parent "ray" both stop propagation once ray is set up
    for name in ("ray.serve", "ray"):
        monkeypatch.setattr(logging.getLogger(name), "propagate", True)
    caplog.set_level(logging.INFO, logger="ray.serve")
    caplog.set_level(logging.INFO, logger="huri.audit")
    return caplog


@pytest.fixture(autouse=True)
def audit_logger_restored(monkeypatch):
    """Building a server routes `huri.audit`: never leak that across tests."""
    audit = logging.getLogger("huri.audit")
    monkeypatch.setattr(audit, "handlers", list(audit.handlers))
    monkeypatch.setattr(audit, "level", audit.level)
