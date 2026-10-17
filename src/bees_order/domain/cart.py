"""The cart, in two halves.

1. build_cart: the draft (what the customer asked for) → priced lines checked against stock.
   Every number comes from the Product rows (spec R1–R12).
2. apply_edit / find_in_cart: change the draft for one parsed add / set / remove.

The draft is a list of plain dicts (ResolvedItem fields) because it lives in graph state.
"""

from bees_order.domain.matching import match, normalize
from bees_order.domain.models import Cart, CartLine, MatchResult, Product, ResolvedItem

MAX_PACKS_PER_LINE = 100  # R10


def units_for(quantity: float, unit: str, p: Product) -> int | None:
    """R1–R3, R11: how many units a quantity means, or None if it isn't a valid quantity.
    A bare number or a pack word means packs; "24 latas" (unit) means units."""
    if quantity <= 0 or quantity != int(quantity):
        return None
    return int(quantity) if unit == "unit" else int(quantity) * p.units_per_pack


# ---- 1. pricing --------------------------------------------------------------------------------

def build_cart(items: list[ResolvedItem], products: dict[str, Product]) -> Cart:
    lines: list[CartLine | str] = []  # a str is a placeholder for a merged SKU line
    merged: dict[str, list] = {}  # sku → [units, ordered in units?]; one line per SKU (R12)
    for it in items:
        p = products.get(it.sku) if it.sku else None
        if it.status == "unknown" or (it.status == "matched" and p is None):  # R6
            lines.append(CartLine(sku=None, name=it.product_text, flag="unknown"))
            continue
        name = p.name if p else it.product_text
        if it.status == "not_added":  # R8: the question was never answered
            lines.append(CartLine(sku=it.sku, name=name, flag="not_added"))
            continue
        units = units_for(it.quantity, it.unit, p)
        if units is None:  # R11
            lines.append(CartLine(sku=p.sku, name=name, flag="invalid_qty"))
            continue
        if p.sku not in merged:
            lines.append(p.sku)  # the line sits where the SKU first appears
            merged[p.sku] = [0, False]
        merged[p.sku][0] += units
        merged[p.sku][1] |= it.unit == "unit"

    out = [_price_line(products[ln], *merged[ln]) if isinstance(ln, str) else ln for ln in lines]
    total = sum(ln.line_total_cents for ln in out if ln.flag in ("ok", "short"))  # R9
    return Cart(lines=out, total_cents=total)


def _price_line(p: Product, requested: int, unit_order: bool) -> CartLine:
    upp = p.units_per_pack
    line = CartLine(sku=p.sku, name=p.name, requested_units=requested,
                    unit_price_cents=p.unit_price_cents)
    if requested > MAX_PACKS_PER_LINE * upp:  # R10
        line.flag = "over_cap"
        return line
    if requested <= p.stock:
        units = requested
    else:  # R4: offer whole packs for pack orders, plain units for unit orders
        available = p.stock if unit_order else (p.stock // upp) * upp
        line.available_unit = "unit" if unit_order else "pack"
        line.available = available if unit_order else available // upp
        if available == 0:  # R5
            line.flag = "unavailable"
            return line
        line.flag = "short"
        units = available
    line.units = units
    line.packs = units // upp
    line.line_total_cents = units * p.unit_price_cents  # R9: DB price only
    return line


# ---- 2. editing the draft ------------------------------------------------------------------

def same_line(item: dict, sku: str | None, text: str) -> bool:
    """Is this draft item the cart line an edit points at? Unknown products have no SKU, so
    they are found by the words the customer typed."""
    if sku:
        return item["sku"] == sku
    return item["sku"] is None and normalize(item["product_text"]) == normalize(text)


def find_in_cart(text: str, items: list[dict], products: dict[str, Product]) -> MatchResult:
    """Match a set/remove target only against what is already in the cart, so "skol" means
    the Skol in the cart, not every Skol in the catalog."""
    skus = list(dict.fromkeys(i["sku"] for i in items if i["sku"]))
    m = match(text, [products[s] for s in skus]) if skus else MatchResult(status="unknown")
    if m.status == "unknown" and any(same_line(i, None, text) for i in items):
        return MatchResult(status="matched")  # e.g. "remove the heineken"
    return m


def requested_units(items: list[dict], sku: str | None, text: str,
                    products: dict[str, Product]) -> int:
    """Units the customer asked for on one cart line (0 for an invalid or unknown line)."""
    p = products.get(sku) if sku else None
    if not p:
        return 0
    return sum(units_for(i["quantity"], i["unit"], p) or 0
               for i in items if same_line(i, sku, text) and i["status"] == "matched")


def apply_edit(e: dict, items: list[dict], products: dict[str, Product]) -> dict:
    """Apply one matched edit to the draft `items` (in place). Returns what happened, which
    replies.describe_changes turns into a sentence."""
    sku, text = e.get("sku"), e["product_text"]
    if e["status"] in ("dropped", "unclear", "not_in_cart") or \
            (e["action"] != "add" and e["status"] == "unknown"):
        return {"kind": e["status"] if e["status"] != "unknown" else "not_in_cart", "text": text}

    before = requested_units(items, sku, text, products)
    new = {"product_text": text, "quantity": e["quantity"], "unit": e["unit"], "sku": sku,
           "status": e["status"] if e["status"] in ("unknown", "not_added") else "matched"}
    if e["action"] == "remove":
        items[:] = [i for i in items if not same_line(i, sku, text)]
        return {"kind": "removed", "sku": sku, "text": text}
    if e["action"] == "set" and any(same_line(i, sku, text) for i in items):
        first = next(n for n, i in enumerate(items) if same_line(i, sku, text))
        if not sku:  # a line found by its words (unknown / not_added) keeps what it was
            new["status"] = items[first]["status"]
        items[:] = [i for n, i in enumerate(items) if n == first or not same_line(i, sku, text)]
        items[first] = new
    else:  # add: a repeated product merges into one cart line (R12)
        items.append(new)
    return {"kind": "changed" if before else "added", "sku": sku, "text": text,
            "before": before, "after": requested_units(items, sku, text, products)}
