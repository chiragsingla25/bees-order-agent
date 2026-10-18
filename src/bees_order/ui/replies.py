"""Every customer-facing word: chat sentences, cart labels, money and quantity formatting.
Code writes all replies; the LLM never does."""

from bees_order.domain.matching import normalize
from bees_order.domain.models import Cart, CartLine, MatchResult, Product, Question

PACK_WORDS = {"cx": "case", "fardo": "pack", "engradado": "crate"}

T = {
    # What a message changed in the cart
    "added": "Added {qty} of {name}.",
    "changed": "{name}: {old} → {new}.",
    "removed": "Removed {name}.",
    "not_in_cart": "“{text}” isn't in your cart, so nothing changed.",
    "not_added": "I couldn't tell which “{text}” you meant, so I didn't add it.",
    "unclear": "I couldn't tell which “{text}” you meant, so nothing changed.",
    "dropped": "OK, I left out “{text}”.",
    # Problems on a cart line
    "short": "Only {avail} of {name} in stock (you asked for {req}), so the cart has {avail}.",
    "unavailable": "{name} is out of stock.",
    "over_cap": "{name}: the limit is 100 {packs} per item, so it won't be ordered.",
    "invalid_qty": "{name}: that quantity isn't valid, so it won't be ordered.",
    "unknown": "We don't carry “{name}”.",
    # Questions
    "clarify": "Which {text} did you mean?",
    "clarify_retry": "Sorry, I didn't get that. Reply with the number or the size.",
    "question_open": "Please answer the question above first.",
    # Answers to the customer's own questions (info_request)
    "cart_empty": "Your cart is empty. Tell me what you need.",
    "cart_total": "Total: {total}.",
    "last_change": "Last change: {change}",
    "product_options": "We have these {text} options:",
    "info_general": "I can tell you what's in your cart, what we just changed, and the price "
                    "and stock of any product (also in the Products tab). What would you like "
                    "to know?",
    # Everything else
    "refuse_off_topic": "Sorry, I can only help with beverage orders. "
                        "Try “10 cases of Brahma cans”.",
    "refuse_restricted": "Sorry, I can't share that. I can help with your order.",
    "no_items": "Tell me what to add, change or remove. When the cart looks right, "
                "tap Place order.",
    "error": "Sorry, I had trouble reading that message. Please try again.",
    "not_configured": "The language model isn't configured in this demo.",
    "placed": "✅ Order #{order_id} placed: {total}. Thanks! Message me any time to start "
              "a new one.",
    "nothing_to_order": "There's nothing to order yet. Tell me what you need.",
    "stock_changed": "Stock changed before I could place the order, so nothing was saved. "
                     "I've updated your cart: check it and tap Place order again.",
    "cleared": "Cart cleared.",
    "limit_session": "You've reached this demo's limit of {n} messages. Please reload later.",
    "limit_daily": "The demo has reached its daily usage limit. Please come back tomorrow.",
}


# The short label on a cart line that won't be (fully) ordered.
LABELS = {"unavailable": "Out of stock", "unknown": "Not in our catalog",
          "over_cap": "Over 100 per item", "invalid_qty": "Invalid quantity",
          "not_added": "Couldn't tell which"}


def t(key: str, **kw) -> str:
    return T[key].format(**kw)


def brl(cents: int) -> str:
    return f"R$ {cents / 100:,.2f}"


def pack_word(p: Product, n: int = 1) -> str:
    word = PACK_WORDS[p.pack_type]
    return word if n == 1 else word + "s"


def qty_text(units: int, p: Product) -> str:
    """Units as the customer counts them: "6 cases", "1 case + 3 units", "24 units"."""
    packs, loose = divmod(units, p.units_per_pack)
    parts = [f"{packs} {pack_word(p, packs)}"] if packs else []
    if loose:
        parts.append(f"{loose} unit" + ("" if loose == 1 else "s"))
    return " + ".join(parts) or "0 units"


def line_problem(ln: CartLine, p: Product | None) -> str | None:
    """The chat sentence for a cart line that isn't fully orderable, or None if it is."""
    if ln.flag == "ok":
        return None
    if ln.flag == "short":
        if ln.available_unit == "pack":
            avail = qty_text(ln.available * p.units_per_pack, p)
            req = qty_text(ln.requested_units, p)
        else:  # a unit order is talked about in units, never packs
            avail, req = f"{ln.available} units", f"{ln.requested_units} units"
        return t("short", name=ln.name, req=req, avail=avail)
    if ln.flag == "over_cap":
        return t("over_cap", name=ln.name, packs=pack_word(p, 2))
    if ln.flag == "not_added":
        return t("not_added", text=ln.name)
    return t(ln.flag, name=ln.name)


def clarify_text(questions: list[dict], products: dict[str, Product], retry: bool) -> str:
    """One message asking about every unclear product, options numbered from 1."""
    out = [t("clarify_retry")] if retry else []
    for q in questions:
        out.append(t("clarify", text=q["product_text"]))
        out += [f"{i}. {products[sku].name}" for i, sku in enumerate(q["candidates"], 1)]
    return "\n".join(out)


def describe_changes(events: list[dict], cart: Cart, products: dict[str, Product]) -> str:
    """The chat reply for one message: what changed (from cart.apply_edit's events), then
    any problem on a line the message touched, each said once."""
    by_sku = {ln.sku: ln for ln in cart.lines if ln.sku}
    by_text = {normalize(ln.name): ln for ln in cart.lines if not ln.sku}
    out, explained = [], set()
    for ev in events:
        kind, sku = ev["kind"], ev.get("sku")
        p = products.get(sku) if sku else None
        if kind == "removed":
            out.append(t("removed", name=p.name if p else ev["text"]))
            continue
        if kind in ("dropped", "unclear", "not_in_cart"):
            out.append(t(kind, text=ev["text"]))
            continue
        line = by_sku.get(sku) if sku else by_text.get(normalize(ev["text"]))
        if line is None:
            continue
        problem = line_problem(line, p)
        if problem:
            if id(line) not in explained:
                out.append(problem)
                explained.add(id(line))
        elif kind == "added":
            out.append(t("added", qty=qty_text(ev["after"], p), name=p.name))
        else:
            out.append(t("changed", name=p.name, old=qty_text(ev["before"], p),
                         new=qty_text(ev["after"], p)))
    return "\n\n".join(out) or t("no_items")


def short_note(ln: CartLine, p: Product) -> str:
    """The note on a short line, the same in the cart panel and in chat."""
    asked = qty_text(ln.requested_units, p) if ln.available_unit == "pack" \
        else f"{ln.requested_units} units"
    return f"Only {ln.available} in stock · you asked for {asked}"


def stock_label(p: Product) -> tuple[str, str]:
    """(kind, text) for a product's stock, the same in the Products tab and in chat."""
    packs = p.stock // p.units_per_pack
    if packs == 0:
        return "err", "Out of stock"
    if packs <= 10:
        return "warn", f"Low · {packs} {pack_word(p, packs)}"
    return "ok", f"{packs} {pack_word(p, packs)} in stock"


def product_info(p: Product) -> str:
    """One product's price and stock, e.g. "Skol Can 350 ml: R$ 37.08 per case of 12
    (R$ 3.09/unit) · 100 cases in stock"."""
    return (f"{p.name}: {brl(p.unit_price_cents * p.units_per_pack)} per {pack_word(p)} of "
            f"{p.units_per_pack} ({brl(p.unit_price_cents)}/unit) · {stock_label(p)[1]}")


def cart_summary(cart: Cart | None, products: dict[str, Product]) -> str:
    """The answer to "what's in my cart?": every line, then the total."""
    if not cart or not cart.lines:
        return t("cart_empty")
    out = []
    for ln in cart.lines:
        p = products.get(ln.sku) if ln.sku else None
        if ln.flag in ("ok", "short"):
            line = f"• {qty_text(ln.units, p)} of {ln.name}: {brl(ln.line_total_cents)}"
            out.append(line + (f" ({short_note(ln, p)})" if ln.flag == "short" else ""))
        else:
            asked = f"{qty_text(ln.requested_units, p)} of " if p and ln.requested_units else ""
            out.append(f"• {asked}{ln.name}: {LABELS[ln.flag]}")
    return "\n".join(out) + "\n\n" + t("cart_total", total=brl(cart.total_cents))


def answer_text(q: Question, found: MatchResult | None, cart: Cart | None,
                last_change: str | None, products: dict[str, Product]) -> str:
    """The answer to an info_request. `found` is the match for a product question."""
    if q.topic == "last_change" and last_change:
        total = brl(cart.total_cents if cart else 0)
        return t("last_change", change=last_change) + "\n\n" + t("cart_total", total=total)
    if q.topic in ("cart", "last_change"):
        return cart_summary(cart, products)
    if found:
        if found.status == "matched":
            return product_info(products[found.sku]) + "."
        if found.status == "ambiguous":
            return "\n".join([t("product_options", text=q.product_text)]
                             + [f"• {product_info(products[sku])}" for sku in found.candidates])
        return t("unknown", name=q.product_text)
    return t("info_general")
