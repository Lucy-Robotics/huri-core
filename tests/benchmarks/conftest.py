import asyncio
import logging

import pytest


@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def production_logging(monkeypatch):
    """The bus logs each publish at INFO in production: measure that cost,
    but send the records nowhere (a null handler, no propagation)."""
    logger = logging.getLogger("ray.serve")
    old = logger.level
    logger.setLevel(logging.INFO)  # not monkeypatch: setLevel clears the level cache
    monkeypatch.setattr(logger, "handlers", [logging.NullHandler()])
    monkeypatch.setattr(logger, "propagate", False)
    yield
    logger.setLevel(old)
