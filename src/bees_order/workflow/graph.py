"""The order workflow: a fixed LangGraph pipeline with one LLM node.

Each browser session has one graph thread, and the thread's state keeps that session's draft
cart between messages. Every call to the graph does one of three things, chosen by `action`:

    message      parse (LLM) → find_products → [ask ⟲ interrupt] → update_cart
                 (a question or refusal is answered in parse and ends there)
    place_order  place_order   (one SQLite transaction, R15)
    clear        clear_cart

The nodes only order the steps. The rules they call live in domain/, the text in ui/replies.
`ask` pauses the graph with `interrupt()` until the customer answers. Code before an
`interrupt()` runs again on resume, so `ask` does nothing with side effects before it.
State holds only plain JSON-able values (Pydantic models are dumped).
"""

import threading
from collections.abc import Callable
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from bees_order.domain.cart import apply_edit, build_cart, find_in_cart
from bees_order.domain.matching import match, normalize, resolve_answer, split_answers
from bees_order.domain.models import Cart, Product, Question, ResolvedItem
from bees_order.services import db
from bees_order.services.llm import NotConfigured, ParseError, ParseResult
from bees_order.ui.replies import answer_text, brl, clarify_text, describe_changes, t

CANCEL_WORDS = {"cancel", "clear", "clear cart", "start over", "never mind", "nevermind",
                "cancelar", "cancela", "esquece", "deixa", "recomecar"}
# Inside a question a plain "no" also means "forget it". Outside one, "no" mustn't clear
# the whole cart, so these extra words only count as answers.
ANSWER_CANCEL_WORDS = CANCEL_WORDS | {"no", "nao", "nope", "stop", "deixa pra la", "nenhum"}


class CartState(TypedDict, total=False):
    # Input for this call
    action: str  # "message" | "place_order" | "clear"
    message: str
    history: list[list[str]]  # earlier [customer message, reply] pairs, oldest first
    # Kept between calls: the session's draft cart
    items: list[dict]  # ResolvedItem dumps: what the customer asked for, in order
    cart: dict | None  # Cart dump: the items priced against stock
    last_change: str | None  # the reply that described the last cart change
    # Working values for this call
    edits: list[dict]  # the parsed items of this message, plus what they matched
    attempts: int  # answers tried for the open question
    status: str | None
    reply: str
    order_id: int | None
    llm: dict | None  # model, attempts and cost of the parse call


def build_graph(conn, parser: Callable[..., ParseResult], lock=None, checkpointer=None):
    lock = lock or threading.RLock()

    def products() -> dict[str, Product]:
        with lock:
            return db.get_products(conn)

    def route(state: CartState) -> str:
        return {"place_order": "place_order", "clear": "clear_cart"}.get(state["action"], "parse")

    # ---- message: parse → find_products → [ask] → update_cart ---------------------------

    def parse(state: CartState):
        cart_names = [ln["name"] for ln in (state.get("cart") or {}).get("lines", [])]
        try:
            result = parser(state["message"], cart_names, state.get("history") or [])
        except NotConfigured:
            return {"status": "not_configured", "reply": t("not_configured")}
        except ParseError:
            return {"status": "error", "reply": t("error")}
        parsed = result.parsed
        update = {"edits": [it.model_dump() for it in parsed.items],
                  "llm": {"model": result.model, "attempts": result.attempts,
                          "cost_usd": result.cost_usd}}
        # R13: items win. Answers and refusals are only for messages with no items.
        if parsed.items:
            return update
        if parsed.intent == "info_request":
            return update | {"status": "answered", "reply": answer(parsed.question, state)}
        key = {"off_topic": "refuse_off_topic", "restricted": "refuse_restricted"}.get(
            parsed.intent, "no_items")
        return update | {"status": "no_items" if key == "no_items" else "refused",
                         "reply": t(key)}

    def answer(q: Question, state: CartState) -> str:
        """Code answers the customer's own question from the cart and SQLite (no LLM)."""
        prods = products()
        found = match(q.product_text, prods) \
            if q.topic == "product" and q.product_text.strip() else None
        cart = Cart(**state["cart"]) if state.get("cart") else None
        return answer_text(q, found, cart, state.get("last_change"), prods)

    def after_parse(state: CartState) -> str:
        return END if state.get("status") else "find_products"

    def find_products(state: CartState):
        prods = products()
        items = state.get("items") or []
        edits = []
        for e in state["edits"]:
            m = match(e["product_text"], prods) if e["action"] == "add" \
                else find_in_cart(e["product_text"], items, prods)  # set / remove: cart only
            edits.append({**e, "status": m.status, "sku": m.sku, "candidates": m.candidates})
        return {"edits": edits, "attempts": 0}

    def after_find(state: CartState) -> str:
        return "ask" if any(e["status"] == "ambiguous" for e in state["edits"]) \
            else "update_cart"

    def ask(state: CartState):
        prods = products()
        attempts = state.get("attempts", 0)
        questions = [e for e in state["edits"] if e["status"] == "ambiguous"]
        answer = str(interrupt({"question": clarify_text(questions, prods,
                                                         retry=attempts > 0)})).strip()
        attempts += 1
        answers = ["cancel"] * len(questions) if normalize(answer) in ANSWER_CANCEL_WORDS \
            else split_answers(answer, len(questions))
        it = iter(answers)
        edits = [e | resolve_answer(e, next(it), attempts, prods) if e["status"] == "ambiguous"
                 else e for e in state["edits"]]
        still_open = any(e["status"] == "ambiguous" for e in edits)
        return Command(goto="ask" if still_open else "update_cart",
                       update={"edits": edits, "attempts": attempts})

    def update_cart(state: CartState):
        prods = products()
        items = [dict(i) for i in state.get("items") or []]
        events = [apply_edit(e, items, prods) for e in state["edits"]]
        cart = build_cart([ResolvedItem(**i) for i in items], prods)
        reply = describe_changes(events, cart, prods)
        # Only a message that changed the draft becomes the "last change".
        changed = items != (state.get("items") or [])
        return {"items": items, "cart": cart.model_dump(), "status": "cart", "reply": reply,
                "last_change": reply if changed else state.get("last_change"), "edits": []}

    # ---- place_order and clear ------------------------------------------------------------

    def place_order(state: CartState):
        cart = Cart(**state["cart"]) if state.get("cart") else Cart()
        if not cart.billable:
            return {"status": "nothing_to_order", "reply": t("nothing_to_order")}
        with lock:
            result = db.commit_order(conn, cart)  # re-checks stock first (R15)
        if result.status == "approved":
            return {"status": "placed", "order_id": result.order_id, "items": [], "cart": None,
                    "last_change": None,
                    "reply": t("placed", order_id=result.order_id,
                               total=brl(result.total_cents))}
        # Stock dropped since the customer saw the cart: nothing was written. Re-price it.
        items = [ResolvedItem(**i) for i in state.get("items") or []]
        return {"status": "stock_changed", "reply": t("stock_changed"), "last_change": None,
                "cart": build_cart(items, products()).model_dump()}

    def clear_cart(state: CartState):
        return {"items": [], "cart": None, "last_change": None, "status": "cleared",
                "reply": t("cleared")}

    g = StateGraph(CartState)
    g.add_node("parse", parse)
    g.add_node("find_products", find_products)
    g.add_node("ask", ask, destinations=("ask", "update_cart"))
    g.add_node("update_cart", update_cart)
    g.add_node("place_order", place_order)
    g.add_node("clear_cart", clear_cart)
    g.add_conditional_edges(START, route, ["parse", "place_order", "clear_cart"])
    g.add_conditional_edges("parse", after_parse, ["find_products", END])
    g.add_conditional_edges("find_products", after_find, ["ask", "update_cart"])
    for node in ("update_cart", "place_order", "clear_cart"):
        g.add_edge(node, END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())
