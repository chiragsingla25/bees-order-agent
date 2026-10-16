"""Resolved items → Cart. Pure code; every number comes from the Product rows (spec R1–R12)."""

from bees_order.schemas import Cart, CartLine, Product, ResolvedItem

MAX_PACKS_PER_LINE = 100


def _invalid(qty: float) -> bool:
    return qty <= 0 or qty != int(qty)


def build_cart(items: list[ResolvedItem], products: dict[str, Product]) -> Cart:
    lines: list[CartLine | str] = []  # a str is a placeholder for a merged SKU line
    merged: dict[str, tuple[int, bool]] = {}  # sku → (units, any unit-order line); R12

    for it in items:
        if it.status == "unknown" or (it.status == "matched" and it.sku not in products):
            lines.append(CartLine(sku=None, name=it.product_text, flag="unknown"))
            continue
        p = products[it.sku] if it.sku else None
        name = p.name if p else it.product_text
        if it.status == "not_added":
            lines.append(CartLine(sku=it.sku, name=name, flag="not_added"))
            continue
        if _invalid(it.quantity):  # R11
            lines.append(CartLine(sku=p.sku, name=name, flag="invalid_qty"))
            continue
        qty = int(it.quantity)
        units = qty if it.unit == "unit" else qty * p.units_per_pack  # R1–R3
        if p.sku not in merged:
            lines.append(p.sku)  # keep message order: the line sits where the SKU first appears
        prev_units, prev_unit_order = merged.get(p.sku, (0, False))
        merged[p.sku] = (prev_units + units, prev_unit_order or it.unit == "unit")

    out = [_price_line(products[ln], *_merged_args(merged[ln])) if isinstance(ln, str) else ln
           for ln in lines]
    total = sum(ln.line_total_cents for ln in out if ln.flag in ("ok", "short"))
    return Cart(lines=out, total_cents=total)


def _merged_args(entry: tuple[int, bool]) -> tuple[int, bool]:
    units, unit_order = entry
    return units, not unit_order


def _price_line(p: Product, requested: int, pack_order: bool) -> CartLine:
    upp = p.units_per_pack
    line = CartLine(sku=p.sku, name=p.name, requested_units=requested,
                    unit_price_cents=p.unit_price_cents)
    if requested > MAX_PACKS_PER_LINE * upp:  # R10
        line.flag = "over_cap"
        return line

    if requested <= p.stock:
        units = requested
    else:  # R4: offer whole packs for pack orders, plain units for unit orders
        available = (p.stock // upp) * upp if pack_order else p.stock
        line.available_unit = "pack" if pack_order else "unit"
        line.available = available // upp if pack_order else available
        if available == 0:  # R5
            line.flag = "unavailable"
            return line
        line.flag = "short"
        units = available

    line.units = units
    line.packs = units // upp
    line.line_total_cents = units * p.unit_price_cents  # R9
    return line
