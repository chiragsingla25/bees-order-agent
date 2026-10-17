"""Every customer-facing sentence. Code writes all replies; the LLM never does."""

from bees_order.schemas import CartLine, Product

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
    # Everything else
    "refuse_off_topic": "Sorry, I can only help with beverage orders. "
                        "Try “10 cases of Brahma cans”.",
    "refuse_data": "Sorry, I can't share that. I can help with your order.",
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
