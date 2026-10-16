"""Phase 1 criterion R7 (matching) plus the Phase 1 clarifications (variants, brand-only)."""

import pytest

from bees_order.matching import extract, match, resolve_among


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
