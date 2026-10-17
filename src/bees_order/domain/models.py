"""The data types every layer shares. Money is integer cents (BRL) everywhere; it is
formatted only at display time."""

from typing import Literal

from pydantic import BaseModel, Field

Unit = Literal["pack", "unit", "none"]
Action = Literal["add", "set", "remove"]  # what a message does to the cart
LineFlag = Literal["ok", "short", "unavailable", "over_cap", "invalid_qty", "not_added", "unknown"]


class ParsedItem(BaseModel):
    """One product mention in a message, as the LLM read it."""

    product_text: str
    quantity: float = 0  # ignored for "remove"
    unit: Unit = "none"
    action: Action = "add"


class ParsedOrder(BaseModel):
    """The LLM's whole output for one message."""

    intent: Literal["order", "off_topic", "data_request"]
    items: list[ParsedItem] = Field(default_factory=list)


class Product(BaseModel):
    """One row of data/products.csv, plus its live stock from SQLite."""

    sku: str
    name: str
    category: str  # "Beer" | "Soft drinks & water" (the Products tab groups by it)
    brand: str
    line: str
    container: str  # lata | latao | litrao | garrafa | longneck | pet
    size_ml: int
    variant: str | None = None  # zero | duplomalte | None
    pack_type: Literal["cx", "fardo", "engradado"]
    units_per_pack: int
    unit_price_cents: int
    aliases: list[str] = Field(default_factory=list)
    stock: int  # units


class MatchResult(BaseModel):
    status: Literal["matched", "ambiguous", "unknown"]
    sku: str | None = None
    candidates: list[str] = Field(default_factory=list)


class ResolvedItem(BaseModel):
    """A product the customer asked for, after matching. The session's draft cart is a list
    of these; build_cart turns it into priced lines."""

    product_text: str
    quantity: float
    unit: Unit = "none"
    sku: str | None = None
    status: Literal["matched", "unknown", "not_added"] = "matched"


class CartLine(BaseModel):
    sku: str | None
    name: str
    packs: int = 0
    units: int = 0  # units in the cart (after shortage reduction)
    requested_units: int = 0
    unit_price_cents: int = 0
    line_total_cents: int = 0
    flag: LineFlag = "ok"
    available: int | None = None  # in packs for pack orders, units for unit orders
    available_unit: Literal["pack", "unit"] | None = None


class Cart(BaseModel):
    lines: list[CartLine] = Field(default_factory=list)
    total_cents: int = 0

    @property
    def billable(self) -> list[CartLine]:
        return [ln for ln in self.lines if ln.flag in ("ok", "short") and ln.units > 0]


class OrderResult(BaseModel):
    status: Literal["approved", "stock_changed", "empty"]
    order_id: int | None = None
    total_cents: int = 0
