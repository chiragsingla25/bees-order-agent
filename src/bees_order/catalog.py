"""Synthetic seed catalog: 29 plausible Brazilian beverage SKUs. Not real AB InBev data.

Names are English (the UI is English-only); the Portuguese words customers type (lata, latão,
litrão, garrafa) are matched through `container`, `line` and the aliases, never the name.
Prices are per unit in BRL cents; stock is in units. Stock values are chosen so the eval
covers ample stock, a shortage (Stella, Guaraná 2 L, Bohemia), zero stock (Spaten) and
an unknown product (anything not listed, e.g. Heineken).
"""


def _p(sku, name, brand, line, container, size_ml, pack_type, upp, price, stock,
       variant=None, aliases=()):
    return {
        "sku": sku, "name": name, "brand": brand, "line": line, "container": container,
        "size_ml": size_ml, "variant": variant, "pack_type": pack_type,
        "units_per_pack": upp, "unit_price_cents": price, "aliases": list(aliases),
        "stock": stock,
    }


CATALOG: list[dict] = [
    # Brahma
    _p("BRA-CHP-LT350", "Brahma Chopp Can 350 ml", "brahma", "brahma chopp", "lata", 350,
       "cx", 12, 329, 600, aliases=["brahminha", "brahma chope"]),
    _p("BRA-CHP-LTA473", "Brahma Chopp Tall Can 473 ml", "brahma", "brahma chopp", "latao", 473,
       "cx", 12, 419, 480, aliases=["brahma chope"]),
    _p("BRA-CHP-LTR1L", "Brahma Chopp Bottle 1 L", "brahma", "brahma chopp", "litrao", 1000,
       "engradado", 12, 799, 360, aliases=["brahma chope"]),
    _p("BRA-CHP-GF600", "Brahma Chopp Bottle 600 ml", "brahma", "brahma chopp", "garrafa", 600,
       "engradado", 24, 549, 480, aliases=["brahma chope"]),
    _p("BRA-DM-LT350", "Brahma Duplo Malte Can 350 ml", "brahma", "brahma duplo malte", "lata",
       350, "cx", 12, 399, 360, variant="duplomalte"),
    _p("BRA-ZR-LT350", "Brahma Zero Can 350 ml", "brahma", "brahma zero", "lata", 350,
       "cx", 12, 359, 240, variant="zero"),
    # Skol
    _p("SKL-LT350", "Skol Can 350 ml", "skol", "skol pilsen", "lata", 350,
       "cx", 12, 309, 1200, aliases=["skolzinha"]),
    _p("SKL-LTA473", "Skol Tall Can 473 ml", "skol", "skol pilsen", "latao", 473,
       "cx", 12, 399, 600),
    _p("SKL-LTR1L", "Skol Bottle 1 L", "skol", "skol pilsen", "litrao", 1000,
       "engradado", 12, 749, 360),
    # Other beers
    _p("ANT-PIL-LT350", "Antarctica Pilsen Can 350 ml", "antarctica", "antarctica pilsen",
       "lata", 350, "cx", 12, 299, 480, aliases=["antartica"]),
    _p("ORI-GF600", "Original Bottle 600 ml", "original", "antarctica original", "garrafa", 600,
       "engradado", 24, 699, 240, aliases=["originalzinha"]),
    _p("BUD-LT350", "Budweiser Can 350 ml", "budweiser", "budweiser", "lata", 350,
       "cx", 12, 389, 480, aliases=["bud"]),
    _p("BUD-LN330", "Budweiser Long Neck 330 ml", "budweiser", "budweiser", "longneck", 330,
       "cx", 24, 499, 480, aliases=["bud"]),
    _p("STE-LN330", "Stella Artois Long Neck 330 ml", "stella artois", "stella artois",
       "longneck", 330, "cx", 24, 589, 144, aliases=["stella"]),
    _p("COR-LN330", "Corona Long Neck 330 ml", "corona", "corona extra", "longneck", 330,
       "cx", 24, 649, 480, aliases=["coronita"]),
    _p("SPA-LT350", "Spaten Can 350 ml", "spaten", "spaten munich", "lata", 350,
       "cx", 12, 429, 0),
    _p("BOH-LT350", "Bohemia Can 350 ml", "bohemia", "bohemia pilsen", "lata", 350,
       "cx", 12, 379, 50),
    _p("BEC-LN330", "Beck's Long Neck 330 ml", "becks", "becks", "longneck", 330,
       "cx", 24, 559, 480, aliases=["beck"]),
    _p("MIC-LN330", "Michelob Ultra Long Neck 330 ml", "michelob", "michelob ultra",
       "longneck", 330, "cx", 24, 569, 480, aliases=["michelob ultra"]),
    # Soft drinks
    _p("GUA-LT350", "Guaraná Antarctica Can 350 ml", "guarana antarctica", "guarana antarctica",
       "lata", 350, "fardo", 12, 279, 720, aliases=["guarana", "guaranazinho"]),
    _p("GUA-PET2L", "Guaraná Antarctica 2 L", "guarana antarctica", "guarana antarctica",
       "pet", 2000, "fardo", 6, 849, 24, aliases=["guarana"]),
    _p("GUA-ZR-LT350", "Guaraná Antarctica Zero Can 350 ml", "guarana antarctica",
       "guarana antarctica zero", "lata", 350, "fardo", 12, 289, 360, variant="zero",
       aliases=["guarana"]),
    _p("PEP-LT350", "Pepsi Can 350 ml", "pepsi", "pepsi", "lata", 350,
       "fardo", 12, 289, 480),
    _p("PEP-PET2L", "Pepsi 2 L", "pepsi", "pepsi", "pet", 2000, "fardo", 6, 899, 240),
    _p("SUK-PET2L", "Sukita Orange 2 L", "sukita", "sukita laranja", "pet", 2000,
       "fardo", 6, 699, 240, aliases=["orange soda"]),
    # Water, isotonic, energy
    _p("H2O-LIM-PET500", "H2OH! Lemon 500 ml", "h2oh", "h2oh limao", "pet", 500,
       "fardo", 12, 449, 360, aliases=["h2o", "lemon water"]),
    _p("AMA-PET500", "AMA Still Water 500 ml", "ama", "agua ama", "pet", 500,
       "fardo", 12, 199, 720, aliases=["agua", "water", "agua mineral"]),
    _p("GAT-PET500", "Gatorade 500 ml", "gatorade", "gatorade", "pet", 500,
       "fardo", 6, 549, 240, aliases=["isotonico"]),
    _p("FUS-LT473", "Fusion Energy Can 473 ml", "fusion", "fusion energy", "lata", 473,
       "fardo", 6, 699, 240, aliases=["energetico", "energy drink"]),
]

SEED_STOCK: dict[str, int] = {p["sku"]: p["stock"] for p in CATALOG}
