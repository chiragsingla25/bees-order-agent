"""Gradio single-page demo (spec Phase 3): WhatsApp-style chat, cart card with Approve/Cancel,
agent trace, stock view, example chips. All logic lives in OrderAgent; this file only renders."""

import json
import os

import gradio as gr

from bees_order import db, replies
from bees_order.agent import OrderAgent, Session, Turn
from bees_order.guards import DailyCallCap, Limits
from bees_order.llm import OpenRouterParser

EXAMPLES = [
    ("🇧🇷 Pedido", "manda 3 cx de brahma lata e 2 fardos de guaraná 2L"),
    ("🌎 Mixed PT/EN", "send 2 cases of Budweiser long neck e 1 fardo de Guaraná 2L"),
    ("❓ Ambíguo", "2 fardos de guaraná"),
    ("⚠️ Falta estoque", "manda 10 cx de stella"),
]

CSS = """
:root { --wa-bg: #efeae2; --wa-out: #d9fdd3; --wa-in: #ffffff; --wa-green: #008069; }
.dark { --wa-bg: #0b141a; --wa-out: #005c4b; --wa-in: #202c33; }
#chat { background: var(--wa-bg); border-radius: 12px; }
#chat .message-row.user-row .message, #chat .user .message-content { background: var(--wa-out) !important; }
#chat .message-row.bot-row .message, #chat .bot .message-content { background: var(--wa-in) !important; }
#header h1 { color: var(--wa-green); margin-bottom: 0; }
#header p { margin: 4px 0; }
.chips button { border-radius: 999px !important; }
.cart-card { min-width: 240px; }
.cart-card .cart-title { font-weight: 700; margin-bottom: 6px; }
.cart-card table { width: 100%; border-collapse: collapse; font-size: 0.95em; }
.cart-card td { padding: 6px 4px; border-bottom: 1px solid rgba(127,127,127,.25); vertical-align: top; }
.cart-card td.amt { text-align: right; white-space: nowrap; }
.cart-card tr.flag td { font-size: 0.92em; }
.cart-card .cart-total { display: flex; justify-content: space-between; font-weight: 700;
  margin-top: 8px; font-size: 1.05em; }
.cart-card .cart-hint { opacity: .75; font-size: .9em; margin-top: 4px; }
#approve { background: var(--wa-green) !important; color: #fff !important; }
"""


def _default_agent() -> OrderAgent:
    path = os.getenv("DB_PATH", "runs/bees.db")
    conn = db.init_db(path)
    db.seed(conn)  # R17: fresh stock on every app start (Space disk is ephemeral anyway)
    daily = DailyCallCap()
    parser = OpenRouterParser(on_call=daily.record)
    return OrderAgent(conn, parser, limits=Limits(daily=daily))


def _trace_md(turn: Turn | None) -> str:
    if not turn or not turn.trace:
        return "_Send an order to see each step here._"
    out = []
    for st in turn.trace:
        o, line = st.get("output"), ""
        if st["step"] == "parse" and isinstance(o, dict):
            if "error" in o:
                line = f"error: {o['error'][:120]}"
            else:
                items = ", ".join(f"{i['quantity']:g} × “{i['product_text']}” ({i['unit']})"
                                  for i in o.get("items", []))
                line = (f"`{o.get('model')}` → intent **{o.get('intent')}**, "
                        f"lang {o.get('language')}; {items or 'no items'}")
        elif st["step"] == "match_stock" and isinstance(o, list):
            parts = []
            for m in o:
                if m["match"] == "matched":
                    parts.append(f"“{m['text']}” → `{m['sku']}` ({m['score']:.0f}, "
                                 f"stock {m['stock_units']} un)")
                elif m["match"] == "ambiguous":
                    parts.append(f"“{m['text']}” → ambiguous {m['candidates']}")
                else:
                    parts.append(f"“{m['text']}” → unknown")
            line = "<br>".join(parts)
        elif st["step"] == "clarify" and isinstance(o, list):
            line = "<br>".join(f"“{m['answer']}” → {m['status']} `{m['sku']}`" for m in o)
        elif st["step"] == "build_cart" and isinstance(o, dict):
            lines = ", ".join(f"`{ln['sku']}` {ln['units']} un {ln['flag']}" for ln in o["lines"])
            line = f"{lines}; total {replies.brl(o['total_cents'])}"
        elif isinstance(o, dict):
            line = ", ".join(f"{k}: {v}" for k, v in o.items() if v is not None)
        out.append(f"**{st['step']}** · {st['ms']:.0f} ms  \n{line}")
    llm = f" · LLM US${turn.llm['cost_usd']:.5f}" if turn.llm else ""
    return "\n\n".join(out) + f"\n\n_Total {turn.ms:.0f} ms{llm}_"


def build_app(agent: OrderAgent | None = None) -> gr.Blocks:
    agent = agent or _default_agent()
    github = os.getenv("GITHUB_URL", "https://github.com/")
    hours = os.getenv("RESET_INTERVAL_H", "2")

    def stock_rows():
        with agent.lock:
            prods = db.get_products(agent.conn)
        rows = []
        for p in prods.values():
            packs, loose = divmod(p.stock, p.units_per_pack)
            rows.append([p.name, f"{p.pack_type} {p.units_per_pack}",
                         f"{packs}" + (f" + {loose} un" if loose else ""),
                         replies.brl(p.unit_price_cents)])
        return rows

    def render(turn: Turn, sess: Session) -> str:
        if turn.status == "cart" and turn.cart:
            from bees_order.schemas import Cart

            with agent.lock:
                prods = db.get_products(agent.conn)
            html = replies.cart_html(sess.language, Cart(**turn.cart), prods)
            changed = replies.t(sess.language, "stock_changed")
            return (changed + "\n\n" + html) if turn.reply.startswith(changed) else html
        return turn.reply

    def outputs(history, sess, turn: Turn | None):
        pending = sess.mode == "approval"
        return (history, sess, gr.update(interactive=pending), gr.update(interactive=pending),
                _trace_md(turn), json.dumps(turn.trace if turn else [], indent=1,
                                            ensure_ascii=False, default=str),
                stock_rows())

    def add_user(text, history, sess):
        sess = sess or Session()
        text = (text or "").strip()
        if text:
            history = [*history, {"role": "user", "content": text}]
        return history, "", sess, text

    def respond(text, history, sess):
        if not text:
            return outputs(history, sess, None)
        turn = agent.handle(sess, text)
        if turn.reply:
            history = [*history, {"role": "assistant", "content": render(turn, sess)}]
        return outputs(history, sess, turn)

    def decide(kind, history, sess):
        sess = sess or Session()
        turn = agent.approve(sess) if kind == "approve" else agent.cancel(sess)
        if turn.reply:
            history = [*history, {"role": "assistant", "content": render(turn, sess)}]
        return outputs(history, sess, turn)

    def reset(history, sess):
        with agent.lock:
            db.reset_stock(agent.conn)
        return stock_rows()

    with gr.Blocks(title="BEES Order Agent — unofficial demo") as demo:
        sess = gr.State(None)
        last_text = gr.State("")
        gr.Markdown(
            "# 🍺 BEES Order Agent\n"
            "Type a beverage order the way a shop owner would — Portuguese or English "
            "shorthand — and get a priced, stock-checked cart to approve. The LLM only reads "
            "the message; code owns every SKU, price and stock number.\n\n"
            f"<small>⚠️ **Unofficial demo, synthetic data, not affiliated with AB InBev.** · "
            f"Shared demo stock resets every {hours} h · [GitHub]({github})</small>",
            elem_id="header")
        with gr.Row(equal_height=False):
            with gr.Column(scale=3, min_width=320):
                chat = gr.Chatbot(elem_id="chat", height=520, show_label=False,
                                  placeholder="Ex.: **manda 3 cx de brahma lata**",
                                  sanitize_html=True, group_consecutive_messages=False)
                with gr.Row(elem_classes="chips"):
                    chips = [gr.Button(label, size="sm", variant="secondary", min_width=130)
                             for label, _ in EXAMPLES]
                with gr.Row():
                    box = gr.Textbox(placeholder="Digite seu pedido… / Type your order…",
                                     show_label=False, scale=5, autofocus=True, max_lines=3)
                    send = gr.Button("Enviar", variant="primary", scale=1, min_width=80)
                with gr.Row():
                    approve = gr.Button("✅ Aprovar / Approve", interactive=False,
                                        elem_id="approve")
                    cancel = gr.Button("✖ Cancelar / Cancel", interactive=False)
            with gr.Column(scale=2, min_width=300):
                with gr.Accordion("🔎 Agent trace (last message)", open=True):
                    trace_md = gr.Markdown(_trace_md(None))
                    with gr.Accordion("Raw trace JSON", open=False):
                        trace_json = gr.Code(language="json", value="[]")
                with gr.Accordion("📦 Current stock", open=False):
                    stock = gr.Dataframe(headers=["Product", "Pack", "Packs in stock",
                                                  "Unit price"],
                                         value=stock_rows(), interactive=False, wrap=True)
                    reset_btn = gr.Button("↺ Reset demo stock", size="sm")

        outs = [chat, sess, approve, cancel, trace_md, trace_json, stock]
        for trigger in (box.submit, send.click):
            trigger(add_user, [box, chat, sess], [chat, box, sess, last_text]).then(
                respond, [last_text, chat, sess], outs)
        for btn, (_, text) in zip(chips, EXAMPLES, strict=True):
            btn.click(lambda h, s, t=text: add_user(t, h, s), [chat, sess],
                      [chat, box, sess, last_text]).then(respond, [last_text, chat, sess], outs)
        approve.click(lambda h, s: decide("approve", h, s), [chat, sess], outs)
        cancel.click(lambda h, s: decide("cancel", h, s), [chat, sess], outs)
        reset_btn.click(reset, [chat, sess], [stock])
        demo.load(stock_rows, None, [stock])
    demo.queue(default_concurrency_limit=8)
    return demo


def launch_kwargs() -> dict:
    return {"css": CSS, "theme": gr.themes.Soft(primary_hue="green")}
