"""Phase 1 criteria for build_cart: R1–R4, R5, R6, R9–R12, plus the empty / negative failure case."""

from bees_order.cart import build_cart
from bees_order.schemas import ResolvedItem


def item(sku, qty, unit="pack", text=None, status="matched"):
    return ResolvedItem(product_text=text or sku or "?", quantity=qty, unit=unit, sku=sku,
                        status=status)


def only(cart):
    assert len(cart.lines) == 1, cart.lines
    return cart.lines[0]


def test_r1_pack_converts_to_units_with_db_price(products):
    ln = only(build_cart([item("BRA-CHP-LT350", 3)], products))
    assert (ln.packs, ln.units, ln.flag) == (3, 36, "ok")
    assert ln.unit_price_cents == products["BRA-CHP-LT350"].unit_price_cents
    assert ln.line_total_cents == 36 * 329


def test_r2_bare_quantity_means_packs(products):
    ln = only(build_cart([item("BRA-CHP-LT350", 5, unit="none")], products))
    assert (ln.packs, ln.units) == (5, 60)


def test_r3_units_and_loose_units_at_unit_price(products):
    ln = only(build_cart([item("BRA-CHP-LT350", 24, unit="unit")], products))
    assert (ln.packs, ln.units) == (2, 24)
    loose = only(build_cart([item("BRA-CHP-LT350", 7, unit="unit")], products))
    assert (loose.units, loose.flag, loose.line_total_cents) == (7, "ok", 7 * 329)


def test_r4_shortage_offers_whole_packs(products):
    # Bohemia: 50 units, 12/pack → 4 whole packs available
    ln = only(build_cart([item("BOH-LT350", 10)], products))
    assert ln.flag == "short"
    assert (ln.available, ln.available_unit, ln.packs, ln.units) == (4, "pack", 4, 48)
    assert ln.line_total_cents == 48 * 379


def test_r4_shortage_for_unit_order_offers_units(products):
    ln = only(build_cart([item("BOH-LT350", 60, unit="unit")], products))
    assert (ln.flag, ln.available, ln.available_unit, ln.units) == ("short", 50, "unit", 50)


def test_r5_out_of_stock_line_flagged_rest_still_built(products):
    cart = build_cart([item("SPA-LT350", 2), item("SKL-LT350", 1)], products)
    spaten, skol = cart.lines
    assert (spaten.flag, spaten.units, spaten.line_total_cents) == ("unavailable", 0, 0)
    assert skol.flag == "ok"
    assert cart.total_cents == skol.line_total_cents


def test_r5_stock_below_one_pack_is_unavailable_for_pack_order(products):
    products["BOH-LT350"].stock = 5
    ln = only(build_cart([item("BOH-LT350", 1)], products))
    assert ln.flag == "unavailable"


def test_r6_unknown_product_reported_not_invented(products):
    cart = build_cart([item(None, 2, text="heineken", status="unknown"),
                       item("SKL-LT350", 1)], products)
    unknown = cart.lines[0]
    assert (unknown.flag, unknown.sku, unknown.name) == ("unknown", None, "heineken")
    assert cart.total_cents == 12 * 309


def test_r9_total_is_sum_of_billable_lines(products):
    cart = build_cart([item("SKL-LT350", 2), item("GUA-PET2L", 1), item("SPA-LT350", 1)],
                      products)
    assert cart.total_cents == 24 * 309 + 6 * 849
    assert cart.currency == "BRL"


def test_r10_over_cap_refused_rest_built(products):
    cart = build_cart([item("SKL-LT350", 9999), item("BRA-CHP-LT350", 1)], products)
    capped, ok = cart.lines
    assert (capped.flag, capped.units, capped.line_total_cents) == ("over_cap", 0, 0)
    assert ok.flag == "ok"


def test_r10_cap_boundary(products):
    products["SKL-LT350"].stock = 10_000
    assert only(build_cart([item("SKL-LT350", 100)], products)).flag == "ok"
    assert only(build_cart([item("SKL-LT350", 101)], products)).flag == "over_cap"
    assert only(build_cart([item("SKL-LT350", 1201, unit="unit")], products)).flag == "over_cap"


def test_r11_invalid_quantities(products):
    for qty in (0, -2, 0.5, 1.5):
        assert only(build_cart([item("SKL-LT350", qty)], products)).flag == "invalid_qty"


def test_r12_same_sku_merged_before_stock_and_cap(products):
    ln = only(build_cart([item("BRA-CHP-LT350", 2), item("BRA-CHP-LT350", 1)], products))
    assert (ln.packs, ln.units) == (3, 36)
    # merged quantity drives the shortage check: 3 + 4 packs of Stella (6 in stock)
    ln = only(build_cart([item("STE-LN330", 3), item("STE-LN330", 4)], products))
    assert (ln.flag, ln.available, ln.packs) == ("short", 6, 6)


def test_r12_pack_plus_units_merge(products):
    ln = only(build_cart([item("BRA-CHP-LT350", 1), item("BRA-CHP-LT350", 3, unit="unit")],
                         products))
    assert (ln.units, ln.packs) == (15, 1)


def test_not_added_line_after_failed_clarification(products):  # R8
    ln = only(build_cart([item("GUA-LT350", 2, status="not_added")], products))
    assert (ln.flag, ln.line_total_cents) == ("not_added", 0)


def test_failure_case_empty_items(products):
    cart = build_cart([], products)
    assert cart.lines == [] and cart.total_cents == 0


def test_user_stated_price_has_no_input_path(products):  # R9: price comes only from Product
    ln = only(build_cart([item("BRA-CHP-LT350", 3, text="brahma lata a R$1 cada")], products))
    assert ln.unit_price_cents == 329


def test_unit_shortage_worded_in_units(products):  # Phase 3 clarified
    from bees_order.replies import cart_text
    unit = build_cart([item("BOH-LT350", 60, unit="unit")], products)
    assert "pediu 60 un, só temos 50 un" in cart_text("pt", unit, products)
    pack = build_cart([item("BOH-LT350", 10)], products)
    assert "pediu 10 cx, só temos 4 cx" in cart_text("pt", pack, products)
    assert "only 50 units in stock" in cart_text("en", unit, products)
