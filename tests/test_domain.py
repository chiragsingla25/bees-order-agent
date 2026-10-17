"""Domain rules (pure code, no network): matching (R7, R8) and the cart (R1–R12)."""

import pytest

from bees_order.domain.cart import build_cart
from bees_order.domain.matching import extract, match, resolve_among
from bees_order.domain.models import ResolvedItem

# ---- from test_matching.py ----------------------------------------------------

@pytest.mark.parametrize(
    ("text", "sku"),
    [
        ("brahma lata", "BRA-CHP-LT350"),  # R7: base SKU, no variant word
        ("brahma latão", "BRA-CHP-LTA473"),  # R7: latão never matches lata
        ("Brahma LATAO", "BRA-CHP-LTA473"),
        ("brahminha", "BRA-CHP-LT350"),  # R7: alias
        ("guaraná zero", "GUA-ZR-LT350"),  # clarified: variant word selects variant
        ("guaraná 2L", "GUA-PET2L"),
        ("guarana 2 litros", "GUA-PET2L"),
        ("skol cans", "SKL-LT350"),  # EN container word
        ("brama lata", "BRA-CHP-LT350"),  # typo still matches
        ("brahma 600", "BRA-CHP-GF600"),  # bare size number
        ("duplo malte lata", "BRA-DM-LT350"),  # variant with no brand word
        ("guaraná antarctica lata", "GUA-LT350"),  # full name beats a subset key
        ("budweiser long neck", "BUD-LN330"),
    ],
)
def test_matched(products, text, sku):
    r = match(text, products)
    assert r.status == "matched", r
    assert r.sku == sku


@pytest.mark.parametrize("text", ["heineken", "coca cola", "itaipava lata", "lata", ""])
def test_unknown_never_invents_a_sku(products, text):  # R6 / R7
    r = match(text, products)
    assert r.status == "unknown"
    assert r.sku is None


def test_guarana_is_ambiguous_between_exactly_the_two_regular_skus(products):  # R7, R8
    r = match("guaraná", products)
    assert r.status == "ambiguous"
    assert set(r.candidates) == {"GUA-LT350", "GUA-PET2L"}


def test_brand_only_skol_is_ambiguous(products):  # clarified: no default container
    r = match("skol", products)
    assert r.status == "ambiguous"
    assert set(r.candidates) == {"SKL-LT350", "SKL-LTA473", "SKL-LTR1L"}


def test_brahma_lata_never_offers_variants(products):  # clarified: variants need the word
    assert "BRA-ZR-LT350" not in match("brahma", products).candidates
    assert "BRA-DM-LT350" not in match("brahma", products).candidates


def test_lata_and_latao_never_cross(products):  # R7 near-collision, both directions
    for text in ["skol lata", "skol latas", "skol latinha"]:
        assert match(text, products).sku == "SKL-LT350"
    for text in ["skol latão", "skol latoes", "skol tall can"]:
        assert match(text, products).sku == "SKL-LTA473"


def test_extract_strips_pack_words_and_reads_sizes():
    assert extract("cx de guaraná 2 L") == ("guarana", None, 2000, None)
    assert extract("fardos de Guaraná Zero lata") == ("guarana", "lata", None, "zero")


def test_clarification_answer_resolves_only_among_offered(products):  # R8
    cands = ["GUA-LT350", "GUA-PET2L"]
    assert resolve_among("2L", cands, products).sku == "GUA-PET2L"
    assert resolve_among("a lata", cands, products).sku == "GUA-LT350"
    assert resolve_among("350 ml", cands, products).sku == "GUA-LT350"
    # an answer naming another product doesn't escape the candidate set
    r = resolve_among("heineken", cands, products)
    assert r.status == "ambiguous"
    assert set(r.candidates) == set(cands)


def test_com_gas_is_a_hard_filter(products):  # Phase 3 clarified
    assert match("água com gás", products).status == "unknown"
    assert match("sparkling water", products).status == "unknown"
    assert match("água", products).sku == "AMA-PET500"
    assert match("água sem gás", products).sku == "AMA-PET500"


# ---- from test_cart.py --------------------------------------------------------

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


def test_unit_shortage_worded_in_units(products):  # Phase 3 clarified, English since Phase 6
    from bees_order.ui.replies import line_problem
    unit = build_cart([item("BOH-LT350", 60, unit="unit")], products).lines[0]
    assert "Only 50 units of Bohemia Can 350 ml in stock (you asked for 60 units)" in \
        line_problem(unit, products["BOH-LT350"])
    pack = build_cart([item("BOH-LT350", 10)], products).lines[0]
    assert "Only 4 cases of Bohemia Can 350 ml in stock (you asked for 10 cases)" in \
        line_problem(pack, products["BOH-LT350"])
