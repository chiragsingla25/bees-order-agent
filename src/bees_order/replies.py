"""Customer-facing text, PT and EN (spec R14). Code writes every reply; the LLM never does."""

from html import escape

from bees_order.schemas import Cart, CartLine, Product

PACK_WORDS = {
    "pt": {"cx": ("cx", "cx"), "fardo": ("fardo", "fardos"), "engradado": ("engradado", "engradados")},
    "en": {"cx": ("case", "cases"), "fardo": ("pack", "packs"), "engradado": ("crate", "crates")},
}

LIMIT_SESSION = ("Você atingiu o limite de {n} pedidos desta demo. Recarregue mais tarde. / "
                 "You've reached this demo's limit of {n} orders per session.")
LIMIT_DAILY = ("A demo atingiu o limite diário de uso. Volte amanhã. / "
               "The demo has reached its daily usage limit. Please come back tomorrow.")

T = {
    "pt": {
        "cart_title": "🛒 *Seu pedido*",
        "total": "*Total: {total}*",
        "approve_hint": "Toque em *Aprovar* para confirmar ou *Cancelar*.",
        "nothing_billable": "Não consegui montar nenhum item para aprovar.",
        "short": "⚠️ {name}: pediu {req}, só temos {avail} — adicionei {avail}",
        "unavailable": "❌ {name}: sem estoque no momento",
        "over_cap": "🚫 {name}: máximo de 100 {packs} por item — não adicionei",
        "invalid_qty": "🚫 {name}: quantidade inválida — não adicionei",
        "not_added": "❓ {name}: não consegui identificar a opção — não adicionei",
        "unknown": "❓ \"{name}\": não temos esse produto",
        "clarify_intro": "Qual opção você quer?",
        "clarify_retry": "Não entendi. Responda com o número ou o tamanho:",
        "refuse_off_topic": "Desculpe, só consigo ajudar com pedidos de bebidas. "
                            "Ex.: \"manda 3 cx de brahma lata\".",
        "refuse_data": "Desculpe, não posso compartilhar esse tipo de informação. "
                       "Posso ajudar com um novo pedido.",
        "no_items": "Não encontrei produtos na mensagem. Ex.: \"manda 3 cx de brahma lata\".",
        "error": "Desculpe, tive um problema para entender a mensagem. Tente de novo.",
        "not_configured": "O modelo de linguagem não está configurado nesta demo.",
        "approved": "✅ Pedido *#{order_id}* confirmado! Total {total}.",
        "cancelled": "Pedido cancelado. Nada foi salvo.",
        "stock_changed": "O estoque mudou enquanto você decidia. Aqui está o pedido atualizado:",
        "pending": "Você tem um pedido aguardando. Toque em *Aprovar* ou *Cancelar*.",
    },
    "en": {
        "cart_title": "🛒 *Your order*",
        "total": "*Total: {total}*",
        "approve_hint": "Tap *Approve* to confirm or *Cancel*.",
        "nothing_billable": "I couldn't put any item in the cart to approve.",
        "short": "⚠️ {name}: you asked for {req}, only {avail} in stock — added {avail}",
        "unavailable": "❌ {name}: out of stock right now",
        "over_cap": "🚫 {name}: max 100 {packs} per item — not added",
        "invalid_qty": "🚫 {name}: invalid quantity — not added",
        "not_added": "❓ {name}: couldn't tell which option — not added",
        "unknown": "❓ \"{name}\": we don't carry that product",
        "clarify_intro": "Which one do you want?",
        "clarify_retry": "Sorry, I didn't get that. Reply with the number or the size:",
        "refuse_off_topic": "Sorry, I can only help with beverage orders. "
                            "E.g. \"send 3 cases of brahma cans\".",
        "refuse_data": "Sorry, I can't share that kind of information. "
                       "I can help with a new order.",
        "no_items": "I didn't find any products in that message. "
                    "E.g. \"send 3 cases of brahma cans\".",
        "error": "Sorry, I had trouble reading that message. Please try again.",
        "not_configured": "The language model isn't configured in this demo.",
        "approved": "✅ Order *#{order_id}* confirmed! Total {total}.",
        "cancelled": "Order cancelled. Nothing was saved.",
        "stock_changed": "Stock changed while you were deciding. Here is the updated order:",
        "pending": "You have an order waiting. Tap *Approve* or *Cancel*.",
    },
}


def t(lang: str, key: str, **kw) -> str:
    return T.get(lang, T["pt"])[key].format(**kw)


def brl(cents: int) -> str:
    reais, c = divmod(cents, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{c:02d}"


def qty_text(lang: str, units: int, p: Product | None) -> str:
    if p is None:
        return str(units)
    one, many = PACK_WORDS[lang][p.pack_type]
    packs, loose = divmod(units, p.units_per_pack)
    unit_word = "un" if lang == "pt" else "units"
    parts = []
    if packs:
        parts.append(f"{packs} {one if packs == 1 else many}")
    if loose:
        parts.append(f"{loose} {unit_word}")
    return " + ".join(parts) or f"0 {unit_word}"


def _flag_line(lang: str, ln: CartLine, products: dict[str, Product]) -> str:
    p = products.get(ln.sku) if ln.sku else None
    if ln.flag == "short":
        if ln.available_unit == "pack":
            req = qty_text(lang, ln.requested_units, p)
            avail = qty_text(lang, ln.available * p.units_per_pack, p)
        else:  # a unit order is talked about in units, never packs
            un = "un" if lang == "pt" else "units"
            req, avail = f"{ln.requested_units} {un}", f"{ln.available} {un}"
        return t(lang, "short", name=ln.name, req=req, avail=avail)
    if ln.flag == "over_cap":
        return t(lang, "over_cap", name=ln.name, packs=PACK_WORDS[lang][p.pack_type][1])
    return t(lang, ln.flag, name=ln.name)


def cart_text(lang: str, cart: Cart, products: dict[str, Product]) -> str:
    out = [t(lang, "cart_title")]
    for ln in cart.lines:
        if ln.flag == "ok":
            p = products.get(ln.sku)
            out.append(f"• {qty_text(lang, ln.units, p)} {ln.name} "
                       f"({ln.units} × {brl(ln.unit_price_cents)}) = {brl(ln.line_total_cents)}")
        else:
            out.append(_flag_line(lang, ln, products))
            if ln.flag == "short":
                out.append(f"  {ln.units} × {brl(ln.unit_price_cents)} = "
                           f"{brl(ln.line_total_cents)}")
    if cart.billable:
        out += ["", t(lang, "total", total=brl(cart.total_cents)), t(lang, "approve_hint")]
    else:
        out += ["", t(lang, "nothing_billable")]
    return "\n".join(out)


def clarify_text(lang: str, pending: list[dict], products: dict[str, Product],
                 retry: bool = False) -> str:
    out = [t(lang, "clarify_retry" if retry else "clarify_intro")]
    for item in pending:
        if len(pending) > 1:
            out.append(f"\n*{item['product_text']}*")
        for i, sku in enumerate(item["candidates"], 1):
            out.append(f"{i}. {products[sku].name}")
    return "\n".join(out)


def cart_html(lang: str, cart: Cart, products: dict[str, Product]) -> str:
    """The cart as a chat card. Every name is escaped: unknown lines echo user text."""
    rows = []
    for ln in cart.lines:
        p = products.get(ln.sku) if ln.sku else None
        if ln.flag == "ok":
            rows.append(
                f"<tr><td><b>{escape(qty_text(lang, ln.units, p))}</b> {escape(ln.name)}"
                f"<br><small>{ln.units} × {brl(ln.unit_price_cents)}</small></td>"
                f"<td class='amt'>{brl(ln.line_total_cents)}</td></tr>")
        else:
            amount = brl(ln.line_total_cents) if ln.flag == "short" else ""
            detail = (f"<br><small>{ln.units} × {brl(ln.unit_price_cents)}</small>"
                      if ln.flag == "short" else "")
            rows.append(f"<tr class='flag flag-{ln.flag}'><td>"
                        f"{escape(_flag_line(lang, ln, products))}{detail}</td>"
                        f"<td class='amt'>{amount}</td></tr>")
    title = escape(t(lang, "cart_title").replace("*", ""))
    if cart.billable:
        foot = (f"<div class='cart-total'>Total <span>{brl(cart.total_cents)}</span></div>"
                f"<div class='cart-hint'>{escape(t(lang, 'approve_hint').replace('*', ''))}</div>")
    else:
        foot = f"<div class='cart-hint'>{escape(t(lang, 'nothing_billable'))}</div>"
    return (f"<div class='cart-card'><div class='cart-title'>{title}</div>"
            f"<table>{''.join(rows)}</table>{foot}</div>")
