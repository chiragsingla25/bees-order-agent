"""The Gradio page: chat on the left; the live cart and the product list on the right.
All ordering logic lives in OrderAgent. This file only renders what the agent returns."""

import os
from html import escape
from pathlib import Path

import gradio as gr

from bees_order.domain.models import Cart, CartLine, Product
from bees_order.services import db
from bees_order.services.llm import OpenRouterParser
from bees_order.ui.replies import LABELS, brl, pack_word, qty_text
from bees_order.workflow.agent import DailyCallCap, Limits, OrderAgent, Session, Turn

CSS = (Path(__file__).parent / "style.css").read_text()

WELCOME = ("Hi! Tell me what you need, the way you'd text your sales rep. For example: "
           "*“10 cases of Brahma cans and 6 packs of Guaraná 2L”*.\n\n"
           "Prices and stock are in the **Products** tab.")
HEAD = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700'
        '&display=swap" rel="stylesheet">'
        # Phone padding lives here, not in CSS: Gradio prefixes every rule inside an @media
        # block with ".gradio-container … .contain", so the outer container can't be reached.
        '<style>@media (max-width: 640px) { .gradio-container { padding: 0 12px !important; }'
        ' .gradio-container .main { padding: 0 !important; } }</style>')


def topbar(github_url: str) -> str:
    return (
        '<div id="topbar"><div class="brand"><span class="mark"><svg viewBox="0 0 24 24" '
        'fill="none" stroke="#0F0F10" stroke-width="2" stroke-linejoin="round"><path d="M12 2.5l8.2 '
        '4.75v9.5L12 21.5l-8.2-4.75v-9.5z"/><path d="M8.5 12.5l2.4 2.4 4.6-5" stroke-linecap="round"/>'
        '</svg></span><div><div class="title">BEES Order Agent<span class="ai">AI</span></div>'
        '<div class="sub">Order drinks by chat · priced and stock-checked</div></div></div>'
        '<div class="right"><span class="chip live">Live stock</span>'
        '<span class="chip">Unofficial demo · synthetic data</span>'
        f'<a class="icon" href="{escape(github_url)}" target="_blank" title="GitHub">'
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M12 .5a11.5 '
        '11.5 0 0 0-3.64 22.41c.58.1.79-.25.79-.56v-2c-3.2.7-3.88-1.37-3.88-1.37-.53-1.33-1.29-1.69'
        '-1.29-1.69-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.71 1.26 3.37.96.1-.75'
        '.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.68 0-1.25.45-2.28 1.19-3.08-.12-.29-.52-1.46.11-'
        '3.04 0 0 .97-.31 3.17 1.18a11 11 0 0 1 5.77 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.58.23 2.75'
        '.11 3.04.74.8 1.19 1.83 1.19 3.08 0 4.41-2.69 5.38-5.26 5.67.41.36.78 1.06.78 2.14v3.17c0 '
        '.31.21.67.8.56A11.5 11.5 0 0 0 12 .5z"/></svg></a></div></div>')



# ---- HTML for the side panel ------------------------------------------------------------------

def cart_html(cart: dict | None, products: dict[str, Product], asking: bool = False,
              changed: frozenset = frozenset()) -> str:
    """The cart card. `changed` holds the lines this message changed; they get highlighted."""
    lines = Cart(**cart).lines if cart else []
    if not lines:
        return ('<div class="card"><div class="card-head"><span class="t">Your cart</span>'
                '<span class="n">0 items</span></div><div class="empty"><div class="ico">🛒</div>'
                '<div class="big">Your cart is empty</div>'
                'Items you mention in the chat show up here.</div></div>')
    rows = "".join(_cart_line(ln, products.get(ln.sku) if ln.sku else None,
                              _key(ln) in changed) for ln in lines)
    count = f"{len(lines)} item" + ("" if len(lines) == 1 else "s")
    hint = ('<div class="hint">Answer the question in chat to place the order.</div>'
            if asking else "")
    return (f'<div class="card"><div class="card-head"><span class="t">Your cart</span>'
            f'<span class="n">{count}</span></div>{rows}'
            f'<div class="total"><span class="k">Total</span>'
            f'<span class="v num">{brl(Cart(**cart).total_cents)}</span></div>{hint}</div>')


def _key(ln: CartLine) -> str:
    return ln.sku or ln.name


def changed_lines(before: dict | None, after: dict | None) -> frozenset:
    """Lines that are new or different after a message (nothing on a first, empty cart)."""
    if not before or not after:
        return frozenset()
    old = {_key(ln): (ln.units, ln.flag) for ln in Cart(**before).lines}
    return frozenset(_key(ln) for ln in Cart(**after).lines if old.get(_key(ln)) != (ln.units, ln.flag))


def _cart_line(ln: CartLine, p: Product | None, changed: bool = False) -> str:
    name = escape(ln.name)  # an unknown product's name is the customer's own words
    kind, pill, amount = "changed" if changed else "", "", brl(ln.line_total_cents)
    if p and ln.units:
        meta = (f"{qty_text(ln.units, p)} of {p.units_per_pack} · "
                f"{brl(p.unit_price_cents * p.units_per_pack)}/{pack_word(p)}")
    else:
        meta = qty_text(ln.requested_units, p) if p and ln.requested_units else ""
    if ln.flag == "short":
        kind += " warn"
        asked = qty_text(ln.requested_units, p) if ln.available_unit == "pack" \
            else f"{ln.requested_units} units"
        pill = f"Only {ln.available} in stock · you asked for {asked}"
    elif ln.flag != "ok":
        kind, pill, amount = "err", LABELS[ln.flag], "—"
    pill_html = f'<div class="pill">{pill}</div>' if pill else ""
    return (f'<div class="line {kind}"><div><div class="name">{name}</div>'
            f'<div class="meta">{meta}</div>{pill_html}</div>'
            f'<div class="amt num">{amount}</div></div>')


def receipt_html(order_id: int, total: str) -> str:
    return (f'<div class="card"><div class="receipt"><span class="pill ok">Order placed</span>'
            f'<div class="no num">#{order_id}</div><div class="sum">{total}</div></div></div>')


def products_html(products: dict[str, Product], query: str | None = "") -> str:
    q = (query or "").strip().lower()  # Gradio sends None for an untouched search box
    groups: dict[str, list[str]] = {}  # category → rows, in CSV order
    for p in products.values():
        if q and q not in p.name.lower():
            continue
        packs = p.stock // p.units_per_pack
        if packs == 0:
            pill = '<span class="pill err">Out of stock</span>'
        elif packs <= 10:
            pill = f'<span class="pill warn">Low · {packs} {pack_word(p, packs)}</span>'
        else:
            pill = f'<span class="pill ok">{packs} {pack_word(p, packs)} in stock</span>'
        row = (f'<div class="line"><div><div class="name">{p.name}</div>'
               f'<div class="meta">{pack_word(p).title()} of {p.units_per_pack} · '
               f'{brl(p.unit_price_cents)}/unit</div>{pill}</div>'
               f'<div class="amt num">{brl(p.unit_price_cents * p.units_per_pack)}'
               f'<div class="meta per">per {pack_word(p)}</div></div></div>')
        groups.setdefault(p.category, []).append(row)
    body = "".join(f'<div class="group">{g}</div>{"".join(rows)}'
                   for g, rows in groups.items() if rows)
    return (f'<div class="card"><div class="card-head"><span class="t">Products</span>'
            f'<span class="n">Prices per pack · live stock</span></div>'
            f'<div class="prod">{body or "<div class=empty>No products match.</div>"}</div></div>')


# ---- the page --------------------------------------------------------------------------------

def _default_agent() -> OrderAgent:
    conn = db.init_db(os.getenv("DB_PATH", "runs/bees.db"))
    db.seed(conn)  # R17: fresh stock on every app start (the Space disk is ephemeral anyway)
    daily = DailyCallCap()
    return OrderAgent(conn, OpenRouterParser(on_call=daily.record), limits=Limits(daily=daily))


def build_app(agent: OrderAgent | None = None) -> gr.Blocks:
    agent = agent or _default_agent()

    def products() -> dict[str, Product]:
        with agent.lock:
            return db.get_products(agent.conn)

    def render(turn: Turn, history: list, sess: Session, query: str, receipt: str = "",
               before: dict | None = None):
        """Everything on the page that can change after a turn."""
        if turn.reply:
            history = [*history, {"role": "assistant", "content": turn.reply}]
        prods = products()
        cart = Cart(**turn.cart) if turn.cart else Cart()
        panel = receipt or cart_html(turn.cart, prods, sess.asking,
                                     changed_lines(before, turn.cart))
        can_order = bool(cart.billable) and not sess.asking
        label = f"Place order · {brl(cart.total_cents)}" if can_order else "Place order"
        return (history, sess, panel,
                gr.update(value=label, interactive=can_order, visible=not receipt),
                gr.update(visible=bool(cart.lines)), products_html(prods, query))

    def add_user_message(text, history, sess):
        sess = sess or Session()
        text = (text or "").strip()
        if text:
            history = [*history, {"role": "user", "content": text}]
        return history, "", sess, text

    def respond(text, history, sess, query):
        before = agent.cart(sess)
        return render(agent.handle(sess, text), history, sess, query, before=before)

    def place(history, sess, query):
        sess = sess or Session()
        before = Cart(**(agent.cart(sess) or {}))
        turn = agent.place_order(sess)
        receipt = ""
        if turn.status == "placed":
            n = len(before.billable)
            receipt = receipt_html(turn.order_id, f"{n} item{'' if n == 1 else 's'} · "
                                                  f"{brl(before.total_cents)}")
        return render(turn, history, sess, query, receipt)

    def clear(history, sess, query):
        sess = sess or Session()
        return render(agent.clear(sess), history, sess, query)

    with gr.Blocks(title="BEES Order Agent") as demo:
        sess = gr.State(None)
        typed = gr.State("")
        gr.HTML(topbar(os.getenv("GITHUB_URL", "https://github.com/")))
        with gr.Row(equal_height=False):
            with gr.Column(scale=3, min_width=300, elem_id="chat-card"):
                chat = gr.Chatbot(value=[{"role": "assistant", "content": WELCOME}],
                                  elem_id="chat", height=560, show_label=False, buttons=[],
                                  group_consecutive_messages=False, container=False)
                box = gr.Textbox(placeholder="Add, change or remove items…", show_label=False,
                                 submit_btn=True, elem_id="composer", max_lines=3,
                                 container=False, autofocus=True)
            with gr.Column(scale=2, min_width=300, elem_id="side"), gr.Tabs(elem_id="side-tabs"):
                with gr.Tab("Cart"):
                    panel = gr.HTML(cart_html(None, {}))
                    place_btn = gr.Button("Place order", interactive=False, elem_id="place")
                    clear_btn = gr.Button("Clear cart", size="sm", visible=False,
                                          elem_id="clear")
                with gr.Tab("Products"):
                    search = gr.Textbox(placeholder="Search products…", show_label=False,
                                        elem_id="search", container=False)
                    plist = gr.HTML(products_html(products()))

        outputs = [chat, sess, panel, place_btn, clear_btn, plist]
        box.submit(add_user_message, [box, chat, sess], [chat, box, sess, typed]).then(
            respond, [typed, chat, sess, search], outputs)
        place_btn.click(place, [chat, sess, search], outputs)
        clear_btn.click(clear, [chat, sess, search], outputs)
        search.change(lambda q: products_html(products(), q), search, plist)
        demo.load(lambda: products_html(products()), None, plist)
    demo.queue(default_concurrency_limit=8)
    return demo


def launch_kwargs() -> dict:
    """Gradio 6 takes styling at launch(), not in Blocks()."""
    return {"css": CSS, "head": HEAD, "theme": gr.themes.Base(), "footer_links": []}
