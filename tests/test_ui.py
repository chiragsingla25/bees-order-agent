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


def test_labels_cover_every_mode_and_element():  # Phase 5 labels
    from bees_order.ui import EXAMPLES, LABELS, header_md
    keys = set(LABELS["auto"])
    for mode in ("pt", "en"):
        assert set(LABELS[mode]) == keys
        assert len(LABELS[mode]["chips"]) == len(EXAMPLES) == 4
        assert len(LABELS[mode]["cols"]) == 4
    assert "/" not in LABELS["en"]["approve"] and "/" not in LABELS["pt"]["approve"]
    assert "Aprovar / Approve" in LABELS["auto"]["approve"]  # Auto keeps bilingual labels
    assert "Demo não oficial" in header_md("pt", "2", "https://x")
    assert "Unofficial demo" in header_md("en", "2", "https://x")


def test_toggle_is_per_session(conn):  # Phase 5: two sessions don't share it
    a, b = Session(), Session()
    a.lang_override = "en"
    agent = OrderAgent(conn, stub)
    assert "Your order" in agent.handle(a, "x").reply
    assert "Seu pedido" in agent.handle(b, "x").reply
