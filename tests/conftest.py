"""Shared fixtures and helpers: a seeded in-memory DB, a stub parser, and small builders."""

from datetime import UTC, datetime

import pytest

from bees_order.domain.models import ParsedItem, ParsedOrder
from bees_order.services import db
from bees_order.services.llm import ParseResult

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


def order(*items) -> ParsedOrder:
    """What the LLM would return. items: (text, quantity, unit) or (text, quantity, unit, action)."""
    return ParsedOrder(intent="order", items=[
        ParsedItem(product_text=i[0], quantity=i[1], unit=i[2], action=(i[3:] or ("add",))[0])
        for i in items])


class StubParser:
    """Stands in for the LLM: message → ParsedOrder (or an exception to raise), from a table.
    Counts calls, remembers the cart names it was shown, and can call a hook per call."""

    def __init__(self, table: dict, on_call=None):
        self.table, self.on_call = table, on_call
        self.calls, self.carts = 0, []

    def __call__(self, msg, cart=()):
        self.calls += 1
        self.carts.append(list(cart))
        if self.on_call:
            self.on_call()
        out = self.table[msg]
        if isinstance(out, Exception):
            raise out
        return ParseResult(out, "stub", 1)


def stock(conn, sku) -> int:
    return conn.execute("SELECT stock FROM products WHERE sku = ?", (sku,)).fetchone()[0]
