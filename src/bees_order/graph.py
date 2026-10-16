"""The order workflow: a fixed LangGraph pipeline with one LLM node and two human pauses.

parse (LLM) → match_stock → [clarify ⟲ interrupt] → build_cart → await_approval (interrupt) → commit

State holds only plain JSON-able values (Pydantic models are dumped), so any checkpointer works.
Code before an `interrupt()` re-runs on resume, so nodes do nothing with side effects before it.
"""

import operator
import re
import threading
import time
from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from bees_order import db, replies
from bees_order.cart import build_cart
from bees_order.llm import NotConfigured, ParseError, ParseResult
from bees_order.matching import match, resolve_among
from bees_order.schemas import Cart, ResolvedItem, TraceStep

MAX_CLARIFY_ATTEMPTS = 2
_SPLIT_RE = re.compile(r"\s*(?:,|;|\n|\be\b|\band\b)\s*")


class OrderState(TypedDict, total=False):
    message: str
    language: str
    status: str | None  # refused | no_items | error | not_configured | empty | approved | cancelled
    reply: str
    parsed: dict | None
    resolved: list[dict]  # ResolvedItem dumps + "candidates"
    clarify_attempts: int
    cart: dict | None
    decision: str | None
    order_id: int | None
    llm: dict | None  # model, attempts, cost of the parse call
    trace: Annotated[list[dict], operator.add]


def _step(name: str, t0: float, inp: Any, out: Any) -> list[dict]:
    ms = round((time.perf_counter() - t0) * 1000, 1)
    return [TraceStep(step=name, input=inp, output=out, ms=ms).model_dump()]


def build_graph(conn, parser: Callable[[str], ParseResult], lock=None,
                checkpointer=None):
    lock = lock or threading.RLock()

    def products():
        with lock:
            return db.get_products(conn)

    def parse_node(state: OrderState):
        t0 = time.perf_counter()
        msg = state["message"]
        try:
            result = parser(msg)
        except NotConfigured as exc:
            return {"status": "not_configured", "reply": replies.t("pt", "not_configured"),
                    "trace": _step("parse", t0, msg, {"error": str(exc)})}
        except ParseError as exc:
            return {"status": "error", "reply": replies.t("pt", "error"),
                    "trace": _step("parse", t0, msg, {"error": str(exc)[:300]})}
        parsed = result.parsed
        lang = parsed.language
        update: OrderState = {
            "parsed": parsed.model_dump(), "language": lang,
            "llm": {"model": result.model, "attempts": result.attempts,
                    "cost_usd": result.cost_usd},
        }
        # R13: items win. A message with order items is processed whatever else it asks;
        # the refusal templates are only for messages with no items.
        if not parsed.items:
            key = {"off_topic": "refuse_off_topic", "data_request": "refuse_data"}.get(
                parsed.intent, "no_items")
            update |= {"status": "no_items" if key == "no_items" else "refused",
                       "reply": replies.t(lang, key)}
        update["trace"] = _step("parse", t0, msg,
                                {**parsed.model_dump(), "model": result.model,
                                 "attempts": result.attempts})
        return update

    def after_parse(state: OrderState) -> str:
        return END if state.get("status") else "match_stock"

    def match_stock_node(state: OrderState):
        t0 = time.perf_counter()
        prods = products()
        resolved, out = [], []
        for it in state["parsed"]["items"]:
            m = match(it["product_text"], prods)
            status = {"matched": "matched", "unknown": "unknown"}.get(m.status, "ambiguous")
            resolved.append({**it, "sku": m.sku, "status": status, "candidates": m.candidates})
            out.append({"text": it["product_text"], "match": m.status, "sku": m.sku,
                        "candidates": m.candidates, "score": round(m.score, 1),
                        "stock_units": prods[m.sku].stock if m.sku else None})
        return {"resolved": resolved, "clarify_attempts": 0,
                "trace": _step("match_stock", t0, [i["product_text"] for i in resolved], out)}

    def after_match(state: OrderState) -> str:
        return "clarify" if any(r["status"] == "ambiguous" for r in state["resolved"]) \
            else "build_cart"

    def clarify_node(state: OrderState):
        lang = state.get("language", "pt")
        prods = products()
        pending = [r for r in state["resolved"] if r["status"] == "ambiguous"]
        question = replies.clarify_text(lang, pending, prods,
                                        retry=state.get("clarify_attempts", 0) > 0)
        answer = interrupt({"kind": "clarify", "reply": question,
                            "options": [[prods[s].name for s in r["candidates"]]
                                        for r in pending]})
        t0 = time.perf_counter()
        parts = [p for p in _SPLIT_RE.split(str(answer).strip()) if p]
        if len(parts) != len(pending):
            # A bare option number can't say which line it answers → re-ask (clarified Phase 3).
            # A container/size word ("lata") may still apply to every line.
            whole = "" if len(pending) > 1 and str(answer).strip().isdigit() else str(answer)
            parts = [whole] * len(pending)
        resolved = [dict(r) for r in state["resolved"]]
        attempts = state.get("clarify_attempts", 0) + 1
        out, k = [], 0
        for r in resolved:
            if r["status"] != "ambiguous":
                continue
            part = parts[k].strip()
            k += 1
            cands = r["candidates"]
            if part.isdigit() and 1 <= int(part) <= len(cands):
                sku = cands[int(part) - 1]
            else:
                m = resolve_among(part, cands, prods)
                sku = m.sku if m.status == "matched" else None
            if sku:
                r |= {"sku": sku, "status": "matched", "candidates": [sku]}
            elif attempts >= MAX_CLARIFY_ATTEMPTS:
                r |= {"status": "not_added", "sku": cands[0] if len(cands) == 1 else None}
            out.append({"text": r["product_text"], "answer": part, "status": r["status"],
                        "sku": r.get("sku")})
        still = any(r["status"] == "ambiguous" for r in resolved)
        return Command(
            goto="clarify" if still else "build_cart",
            update={"resolved": resolved, "clarify_attempts": attempts,
                    "trace": _step("clarify", t0, str(answer), out)},
        )

    def _cart_update(state: OrderState, prefix: str | None = None):
        t0 = time.perf_counter()
        prods = products()
        items = [ResolvedItem(**{k: r.get(k) for k in
                                 ("product_text", "quantity", "unit", "sku", "status")})
                 for r in state["resolved"]]
        cart = build_cart(items, prods)
        lang = state.get("language", "pt")
        text = replies.cart_text(lang, cart, prods)
        if prefix:
            text = prefix + "\n\n" + text
        update = {"cart": cart.model_dump(), "reply": text,
                  "trace": _step("build_cart", t0, len(items),
                                 {"lines": [ln.model_dump(include={"sku", "units", "flag",
                                                                   "line_total_cents"})
                                            for ln in cart.lines],
                                  "total_cents": cart.total_cents})}
        if not cart.billable:
            update["status"] = "empty"
        return update

    def build_cart_node(state: OrderState):
        return _cart_update(state)

    def after_cart(state: OrderState) -> str:
        return END if state.get("status") == "empty" else "await_approval"

    def await_approval_node(state: OrderState):
        decision = interrupt({"kind": "approval", "reply": state["reply"],
                              "cart": state["cart"]})
        if decision not in ("approve", "cancel"):
            return Command(goto="await_approval")
        t0 = time.perf_counter()  # after the interrupt, so a resume doesn't log twice
        return Command(goto="commit", update={
            "decision": decision,
            "trace": _step("await_approval", t0, "cart shown", {"decision": decision})})

    def commit_node(state: OrderState):
        t0 = time.perf_counter()
        lang = state.get("language", "pt")
        if state["decision"] == "cancel":
            return Command(goto=END, update={
                "status": "cancelled", "reply": replies.t(lang, "cancelled"),
                "trace": _step("commit", t0, "cancel", {"status": "cancelled"})})
        with lock:
            result = db.commit_order(conn, Cart(**state["cart"]))
        step = _step("commit", t0, "approve", result.model_dump())
        if result.status == "approved":
            return Command(goto=END, update={
                "status": "approved", "order_id": result.order_id, "trace": step,
                "reply": replies.t(lang, "approved", order_id=result.order_id,
                                   total=replies.brl(result.total_cents))})
        update = _cart_update(state, prefix=replies.t(lang, "stock_changed"))  # R15
        update["trace"] = step + update["trace"]
        update["decision"] = None
        return Command(goto=END if update.get("status") == "empty" else "await_approval",
                       update=update)

    g = StateGraph(OrderState)
    g.add_node("parse", parse_node)
    g.add_node("match_stock", match_stock_node)
    g.add_node("clarify", clarify_node, destinations=("clarify", "build_cart"))
    g.add_node("build_cart", build_cart_node)
    g.add_node("await_approval", await_approval_node, destinations=("await_approval", "commit"))
    g.add_node("commit", commit_node, destinations=("await_approval", END))
    g.add_edge(START, "parse")
    g.add_conditional_edges("parse", after_parse, ["match_stock", END])
    g.add_conditional_edges("match_stock", after_match, ["clarify", "build_cart"])
    g.add_conditional_edges("build_cart", after_cart, ["await_approval", END])
    return g.compile(checkpointer=checkpointer or InMemorySaver())
