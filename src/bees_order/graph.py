"""The order workflow: a fixed LangGraph pipeline with one LLM node.

Each browser session has one graph thread, and the thread's state keeps that session's draft
cart between messages. Every call to the graph does one of three things, chosen by `action`:

    message      parse (LLM) → find_products → [ask ⟲ interrupt] → update_cart
    place_order  place_order   (one SQLite transaction, R15)
    clear        clear_cart

`ask` pauses the graph with `interrupt()` until the customer answers. Code before an
`interrupt()` runs again on resume, so `ask` does nothing with side effects before it.
State holds only plain JSON-able values (Pydantic models are dumped).
"""

import re
import threading
from collections.abc import Callable
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from bees_order import db, replies
from bees_order.cart import build_cart
from bees_order.llm import NotConfigured, ParseError, ParseResult
from bees_order.matching import match, normalize, resolve_among
from bees_order.replies import t
from bees_order.schemas import Cart, MatchResult, Product, ResolvedItem

MAX_ASKS = 2  # a second unclear answer gives up on that product (R8)
CANCEL_WORDS = {"cancel", "clear", "clear cart", "start over", "never mind", "nevermind",
                "cancelar", "cancela", "esquece", "deixa", "recomecar"}
# Inside a question a plain "no" also means "forget it". Outside one, "no" mustn't clear
# the whole cart, so these extra words only count as answers.
ANSWER_CANCEL_WORDS = CANCEL_WORDS | {"no", "nao", "nope", "stop", "deixa pra la", "nenhum"}
_SPLIT_RE = re.compile(r"\s*(?:,|;|\n|\be\b|\band\b)\s*")


class CartState(TypedDict, total=False):
    # Input for this call
    action: str  # "message" | "place_order" | "clear"
    message: str
    # Kept between calls: the session's draft cart
    items: list[dict]  # ResolvedItem dumps: what the customer asked for, in order
    cart: dict | None  # Cart dump: the items priced against stock
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
            result = parser(state["message"], cart_names)
        except NotConfigured:
            return {"status": "not_configured", "reply": t("not_configured")}
        except ParseError:
            return {"status": "error", "reply": t("error")}
        parsed = result.parsed
        update = {"edits": [it.model_dump() for it in parsed.items],
                  "llm": {"model": result.model, "attempts": result.attempts,
                          "cost_usd": result.cost_usd}}
        # R13: items win. The refusal replies are only for messages with no items.
        if not parsed.items:
            key = {"off_topic": "refuse_off_topic", "data_request": "refuse_data"}.get(
                parsed.intent, "no_items")
            update |= {"status": "no_items" if key == "no_items" else "refused",
                       "reply": t(key)}
        return update

    def after_parse(state: CartState) -> str:
        return END if state.get("status") else "find_products"

    def find_products(state: CartState):
        prods = products()
        items = state.get("items") or []
        edits = []
        for e in state["edits"]:
            if e["action"] == "add":
                m = match(e["product_text"], prods)
            else:  # set / remove only look at what is already in the cart
                m = _find_in_cart(e["product_text"], items, prods)
            edits.append({**e, "status": m.status, "sku": m.sku, "candidates": m.candidates})
        return {"edits": edits, "attempts": 0}

    def after_find(state: CartState) -> str:
        return "ask" if any(e["status"] == "ambiguous" for e in state["edits"]) \
            else "update_cart"

    def ask(state: CartState):
        prods = products()
        attempts = state.get("attempts", 0)
        open_qs = [e for e in state["edits"] if e["status"] == "ambiguous"]
        answer = str(interrupt({"question": replies.clarify_text(open_qs, prods,
                                                                 retry=attempts > 0)})).strip()
        attempts += 1
        if normalize(answer) in ANSWER_CANCEL_WORDS:
            answers = ["cancel"] * len(open_qs)
        else:
            answers = [p for p in _SPLIT_RE.split(answer) if p]
            if len(answers) != len(open_qs):
                # One answer for several questions: a bare number can't say which one it
                # answers (→ ask again), but a size or container word ("lata") fits them all.
                one_number = len(open_qs) > 1 and answer.isdigit()
                answers = ["" if one_number else answer] * len(open_qs)
        edits, k = [], 0
        for e in state["edits"]:
            e = dict(e)
            if e["status"] == "ambiguous":
                e |= _answer(e, answers[k], attempts, prods)
                k += 1
            edits.append(e)
        still_open = any(e["status"] == "ambiguous" for e in edits)
        return Command(goto="ask" if still_open else "update_cart",
                       update={"edits": edits, "attempts": attempts})

    def update_cart(state: CartState):
        prods = products()
        items = [dict(i) for i in state.get("items") or []]
        events = [_apply(e, items, prods) for e in state["edits"]]
        cart = build_cart([ResolvedItem(**i) for i in items], prods)
        return {"items": items, "cart": cart.model_dump(), "status": "cart",
                "reply": _describe(events, cart, prods), "edits": []}

    # ---- place_order and clear ------------------------------------------------------------

    def place_order(state: CartState):
        cart = Cart(**state["cart"]) if state.get("cart") else Cart()
        if not cart.billable:
            return {"status": "nothing_to_order", "reply": t("nothing_to_order")}
        with lock:
            result = db.commit_order(conn, cart)  # re-checks stock first (R15)
        if result.status == "approved":
            return {"status": "placed", "order_id": result.order_id, "items": [], "cart": None,
                    "reply": t("placed", order_id=result.order_id,
                               total=replies.brl(result.total_cents))}
        # Stock dropped since the customer saw the cart: nothing was written. Re-price it.
        items = [ResolvedItem(**i) for i in state.get("items") or []]
        return {"status": "stock_changed", "reply": t("stock_changed"),
                "cart": build_cart(items, products()).model_dump()}

    def clear_cart(state: CartState):
        return {"items": [], "cart": None, "status": "cleared", "reply": t("cleared")}

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


# ---- helpers (pure functions over plain dicts) ----------------------------------------------

def _same_line(item: dict, sku: str | None, text: str) -> bool:
    """Is this draft item the cart line an edit points at? Unknown products have no SKU, so
    they are found by the words the customer typed."""
    if sku:
        return item["sku"] == sku
    return item["sku"] is None and normalize(item["product_text"]) == normalize(text)


def _find_in_cart(text: str, items: list[dict], prods: dict[str, Product]) -> MatchResult:
    skus = list(dict.fromkeys(i["sku"] for i in items if i["sku"]))
    m = match(text, [prods[s] for s in skus]) if skus else MatchResult(status="unknown")
    if m.status != "unknown":
        return m
    if any(_same_line(i, None, text) for i in items):  # e.g. "remove the heineken"
        return MatchResult(status="matched")
    return MatchResult(status="unknown")


def _answer(edit: dict, answer: str, attempts: int, prods: dict[str, Product]) -> dict:
    """Resolve one open question from the customer's answer: an option number or words."""
    cands = edit["candidates"]
    if answer == "cancel":
        return {"status": "dropped"}
    if answer.isdigit() and 1 <= int(answer) <= len(cands):
        return {"status": "matched", "sku": cands[int(answer) - 1], "candidates": []}
    m = resolve_among(answer, cands, prods)
    if m.status == "matched":
        return {"status": "matched", "sku": m.sku, "candidates": []}
    if attempts >= MAX_ASKS:
        return {"status": "not_added" if edit["action"] == "add" else "unclear"}
    return {}


def _units(item: dict, prods: dict[str, Product]) -> int:
    p = prods.get(item["sku"]) if item["sku"] else None
    if not p or item["status"] != "matched":
        return 0
    q = item["quantity"]
    if q <= 0 or q != int(q):
        return 0
    return int(q) if item["unit"] == "unit" else int(q) * p.units_per_pack


def _apply(e: dict, items: list[dict], prods: dict[str, Product]) -> dict:
    """Apply one edit to the draft `items` (in place). Returns what happened, for the reply."""
    sku, text = e.get("sku"), e["product_text"]
    if e["status"] in ("dropped", "unclear", "not_in_cart") or \
            (e["action"] != "add" and e["status"] == "unknown"):
        return {"kind": e["status"] if e["status"] != "unknown" else "not_in_cart", "text": text}

    def requested() -> int:
        return sum(_units(i, prods) for i in items if _same_line(i, sku, text))

    before = requested()
    new = {"product_text": text, "quantity": e["quantity"], "unit": e["unit"], "sku": sku,
           "status": e["status"] if e["status"] in ("unknown", "not_added") else "matched"}
    if e["action"] == "remove":
        items[:] = [i for i in items if not _same_line(i, sku, text)]
        return {"kind": "removed", "sku": sku, "text": text}
    if e["action"] == "set" and any(_same_line(i, sku, text) for i in items):
        first = next(n for n, i in enumerate(items) if _same_line(i, sku, text))
        if not sku:  # a line found by its words (unknown / not_added) keeps what it was
            new["status"] = items[first]["status"]
        items[:] = [i for n, i in enumerate(items) if n == first or not _same_line(i, sku, text)]
        items[first] = new
    else:  # add (a repeated product merges into one cart line, R12)
        items.append(new)
    return {"kind": "changed" if before else "added", "sku": sku, "text": text,
            "before": before, "after": requested()}


def _describe(events: list[dict], cart: Cart, prods: dict[str, Product]) -> str:
    """The chat reply for one message: what changed, then any problem on a touched line."""
    lines_by_sku = {ln.sku: ln for ln in cart.lines if ln.sku}
    lines_by_text = {normalize(ln.name): ln for ln in cart.lines if not ln.sku}
    out, explained = [], set()
    for ev in events:
        kind, sku = ev["kind"], ev.get("sku")
        p = prods.get(sku) if sku else None
        if kind == "removed":
            out.append(t("removed", name=p.name if p else ev["text"]))
            continue
        if kind in ("dropped", "unclear", "not_in_cart"):
            out.append(t(kind, text=ev["text"]))
            continue
        line = lines_by_sku.get(sku) if sku else lines_by_text.get(normalize(ev["text"]))
        if line is None:
            continue
        problem = replies.line_problem(line, p)
        if problem:
            if id(line) not in explained:  # say each problem once per message
                out.append(problem)
                explained.add(id(line))
        elif kind == "added":
            out.append(t("added", qty=replies.qty_text(ev["after"], p), name=p.name))
        else:
            out.append(t("changed", name=p.name, old=replies.qty_text(ev["before"], p),
                         new=replies.qty_text(ev["after"], p)))
    return "\n\n".join(out) or t("no_items")
