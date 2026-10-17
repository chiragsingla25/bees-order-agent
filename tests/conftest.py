from datetime import UTC, datetime

import pytest

from bees_order import db

T0 = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch):
    """Tests never send anything to LangSmith, whatever the developer's shell has set."""
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)


@pytest.fixture
def conn():
    c = db.init_db(":memory:")
    db.seed(c, now=T0)
    yield c
    c.close()


@pytest.fixture
def products(conn):
    return db.get_products(conn)
