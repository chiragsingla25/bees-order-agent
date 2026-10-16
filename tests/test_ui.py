"""Phase 3: the UI builds against a stub agent, the trace renders every step, and the cart
card escapes user-typed text (unknown product names are echoed back)."""

import gradio as gr

from bees_order.agent import OrderAgent, Session
from bees_order.cart import build_cart
from bees_order.llm import ParseResult
from bees_order.replies import cart_html
from bees_order.schemas import ParsedItem, ParsedOrder, ResolvedItem
from bees_order.ui import _trace_md, build_app


def stub(msg):
    return ParseResult(ParsedOrder(intent="order", language="pt", items=[
        ParsedItem(product_text="brahma lata", quantity=3, unit="pack")]), "stub", 1)


def test_app_builds_with_a_stub_agent(conn):
    assert isinstance(build_app(OrderAgent(conn, stub)), gr.Blocks)


def test_trace_markdown_names_each_step(conn):
    agent = OrderAgent(conn, stub)
    s = Session()
    md = _trace_md(agent.handle(s, "x"))
    for step in ("parse", "match_stock", "build_cart"):
        assert f"**{step}**" in md
    assert "BRA-CHP-LT350" in md
    assert "**commit**" in _trace_md(agent.approve(s))


def test_cart_card_escapes_user_text(products):
    evil = "<img src=x onerror=alert(1)>"
    cart = build_cart([ResolvedItem(product_text=evil, quantity=1, status="unknown")], products)
    html = cart_html("pt", cart, products)
    assert "<img" not in html and "&lt;img" in html
