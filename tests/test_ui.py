"""The page builds against a stub agent, and the side-panel HTML renders the cart and the
product list correctly. Unknown product names echo the customer's text, so they're escaped."""

import gradio as gr

from bees_order.domain.cart import build_cart
from bees_order.domain.models import ResolvedItem
from bees_order.ui.page import build_app, cart_html, products_html
from bees_order.workflow.agent import OrderAgent
from tests.conftest import StubParser


def test_app_builds_with_a_stub_agent(conn):
    assert isinstance(build_app(OrderAgent(conn, StubParser({}))), gr.Blocks)


def test_cart_html_escapes_user_text(products):
    evil = "<img src=x onerror=alert(1)>"
    cart = build_cart([ResolvedItem(product_text=evil, quantity=1, status="unknown")], products)
    html = cart_html(cart.model_dump(), products)
    assert "<img" not in html and "&lt;img" in html and "Not in our catalog" in html


def test_cart_html_shows_lines_notes_and_total(products):
    items = [ResolvedItem(product_text="b", quantity=10, unit="pack", sku="BRA-CHP-LT350"),
             ResolvedItem(product_text="g", quantity=6, unit="pack", sku="GUA-PET2L"),
             ResolvedItem(product_text="s", quantity=2, unit="pack", sku="SPA-LT350")]
    html = cart_html(build_cart(items, products).model_dump(), products)
    assert "10 cases of 12 · R$ 39.48/case" in html and "R$ 394.80" in html
    assert "Only 4 in stock · you asked for 6 packs" in html
    assert 'class="line err"' in html and "Out of stock" in html
    assert "R$ 598.56" in html  # 394.80 + 203.76; Spaten not counted
    assert "Your cart is empty" in cart_html(None, products)


def test_products_html_lists_prices_and_stock_pills(products):
    html = products_html(products)
    assert html.count('class="line"') == len(products)
    assert "Brahma Chopp Can 350 ml" in html and "R$ 39.48" in html and "per case" in html
    assert "50 cases in stock" in html  # 600 units / 12
    assert "Low · 4 packs" in html  # Guaraná 2 L: 24 units / 6
    assert "Out of stock" in html  # Spaten
    assert products_html(products, None) == html  # untouched search box sends None
    filtered = products_html(products, "guaraná")
    assert filtered.count('class="line"') == 3 and "Brahma" not in filtered


def test_changed_lines_highlight_only_what_a_message_changed(products):  # Phase 7
    from bees_order.ui.page import changed_lines

    def cart(*items):
        return build_cart([ResolvedItem(product_text=s, quantity=q, unit="pack", sku=s)
                           for s, q in items], products).model_dump()

    before = cart(("BRA-CHP-LT350", 10), ("SKL-LT350", 5))
    after = cart(("BRA-CHP-LT350", 6), ("SKL-LT350", 5), ("BUD-LN330", 2))
    assert changed_lines(before, after) == {"BRA-CHP-LT350", "BUD-LN330"}
    assert changed_lines(None, after) == frozenset()  # a first order isn't "a change"
    html = cart_html(after, products, changed=changed_lines(before, after))
    assert html.count('class="line changed"') == 2


def test_page_chrome_and_header():  # Phase 7: no footer, Geist font, safe GitHub link
    from bees_order.ui.page import launch_kwargs, topbar

    kw = launch_kwargs()
    assert kw["footer_links"] == [] and "Geist" in kw["head"] and "--accent: #FFD60A" in kw["css"]
    # Always light (Chirag 2026-10-18): the head pins ?__theme=light; no dark palette is left.
    assert 'get("__theme") !== "light"' in kw["head"]  # also overrides ?__theme=dark (HF)
    assert 'searchParams.set("__theme", "light")' in kw["head"] and ".dark" not in kw["css"]
    bar = topbar('https://github.com/x"><script>')
    assert "<script>" not in bar and "BEES Order Agent" in bar and "Unofficial demo" in bar
