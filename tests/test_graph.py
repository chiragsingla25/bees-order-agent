"""Phase 6 criteria for the chat cart, driven through OrderAgent with a stub parser.

The stub maps a message to a ParsedOrder, so these tests check what code does with a parse:
matching, editing the draft cart, asking, placing and clearing. No network.
"""

import pytest

from bees_order import db
from bees_order.agent import OrderAgent, Session
from bees_order.catalog import CATALOG
from bees_order.llm import NotConfigured, ParseError, ParseResult
from bees_order.schemas import ParsedItem, ParsedOrder


def order(*items):
    """items: (product_text, quantity, unit) or (product_text, quantity, unit, action)."""
    return ParsedOrder(intent="order", items=[
        ParsedItem(product_text=i[0], quantity=i[1], unit=i[2], action=(i[3:] or ("add",))[0])
        for i in items])


TABLE = {
    "brahma": order(("brahma lata", 3, "pack")),
    "brahma 6": order(("brahma lata", 6, "pack")),
    "make brahma 2": order(("brahma", 2, "pack", "set")),
    "make brahma 500": order(("brahma", 500, "pack", "set")),
    "drop brahma": order(("brahma", 0, "pack", "remove")),
    "drop pepsi": order(("pepsi", 0, "pack", "remove")),
    "make pepsi 2": order(("pepsi", 2, "pack", "set")),
    "skol": order(("skol lata", 2, "pack")),
    "two": order(("brahma lata", 3, "pack"), ("guaraná", 2, "pack")),
    "twoamb": order(("guaraná", 2, "pack"), ("skol", 1, "pack")),
    "stella": order(("stella", 5, "pack")),
    "spaten+heineken": order(("spaten lata", 2, "pack"), ("heineken", 1, "pack")),
    "drop heineken": order(("heineken", 0, "pack", "remove")),
    "make guarana 4": order(("guaraná", 4, "pack", "set")),
    "no": order(("skol lata", 1, "pack")),  # if "no" ever reached the parser
    "poem": ParsedOrder(intent="off_topic"),
    "leak": ParsedOrder(intent="data_request"),
    "empty": ParsedOrder(intent="order"),
    "mixed_leak": ParsedOrder(intent="data_request",
                              items=[ParsedItem(product_text="skol lata", quantity=2, unit="pack")]),
    "boom": ParseError("bad output twice"),
    "nokey": NotConfigured("no key"),
}


class StubParser:
    def __init__(self):
        self.calls = 0
        self.carts = []  # the cart names the parser was shown, per call

    def __call__(self, msg, cart=()):
        self.calls += 1
        self.carts.append(list(cart))
        out = TABLE[msg]
        if isinstance(out, Exception):
            raise out
        return ParseResult(out, "stub", 1)


@pytest.fixture
def agent(conn):
    return OrderAgent(conn, StubParser())


def lines(turn):
    """The cart as {sku-or-name: (units, flag)}."""
    return {ln["sku"] or ln["name"]: (ln["units"], ln["flag"])
            for ln in (turn.cart or {}).get("lines", [])}


def stock(conn, sku):
    return conn.execute("SELECT stock FROM products WHERE sku = ?", (sku,)).fetchone()[0]


# ---- one LLM call per message, and none for answers or buttons -------------------------------

def test_one_llm_call_per_message_and_none_for_answers_clear_or_place(agent):
    s = Session()
    agent.handle(s, "two")  # 1 call → question
    agent.handle(s, "2L")  # answer: no call
    agent.place_order(s)
    agent.handle(s, "brahma")  # 2
    agent.handle(s, "clear cart")
    agent.clear(s)
    assert agent.parser.calls == 2


def test_parser_sees_the_names_on_the_cart(agent):
    s = Session()
    agent.handle(s, "brahma")
    agent.handle(s, "make brahma 2")
    assert agent.parser.carts == [[], ["Brahma Chopp Can 350 ml"]]


# ---- add, merge, set, remove ------------------------------------------------------------------

def test_add_appends_and_a_repeated_product_merges(agent):  # R12, clarified 2026-10-17
    s = Session()
    t = agent.handle(s, "brahma")
    assert lines(t) == {"BRA-CHP-LT350": (36, "ok")}
    assert t.reply == "Added 3 cases of Brahma Chopp Can 350 ml."
    agent.handle(s, "skol")
    t = agent.handle(s, "brahma 6")
    assert lines(t) == {"BRA-CHP-LT350": (108, "ok"), "SKL-LT350": (24, "ok")}
    assert t.reply == "Brahma Chopp Can 350 ml: 3 cases → 9 cases."


def test_set_changes_the_quantity_of_the_line_in_the_cart(agent):
    s = Session()
    agent.handle(s, "brahma")
    agent.handle(s, "brahma 6")  # two draft items for one SKU
    t = agent.handle(s, "make brahma 2")
    assert lines(t) == {"BRA-CHP-LT350": (24, "ok")}
    assert t.reply == "Brahma Chopp Can 350 ml: 9 cases → 2 cases."


def test_remove_drops_the_line(agent):
    s = Session()
    agent.handle(s, "brahma")
    agent.handle(s, "skol")
    t = agent.handle(s, "drop brahma")
    assert lines(t) == {"SKL-LT350": (24, "ok")}
    assert t.reply == "Removed Brahma Chopp Can 350 ml."


def test_set_and_remove_only_look_at_the_cart(agent):
    """"brahma" alone is ambiguous in the catalog, but means the one Brahma in the cart."""
    s = Session()
    agent.handle(s, "brahma")
    t = agent.handle(s, "make brahma 2")
    assert t.status == "cart" and lines(t) == {"BRA-CHP-LT350": (24, "ok")}


@pytest.mark.parametrize("msg", ["drop pepsi", "make pepsi 2"])
def test_target_not_in_cart_changes_nothing(agent, msg):
    s = Session()
    before = lines(agent.handle(s, "brahma"))
    t = agent.handle(s, msg)
    assert lines(t) == before
    assert t.reply == "“pepsi” isn't in your cart, so nothing changed."


# ---- previously-correct inputs: a first message on an empty cart -----------------------------

def test_first_order_on_empty_cart_gives_the_v1_lines(agent):
    t = agent.handle(Session(), "two")
    assert t.status == "clarify"
    t2 = agent.handle(Session(), "brahma")
    assert lines(t2) == {"BRA-CHP-LT350": (36, "ok")}
    assert t2.cart["total_cents"] == 36 * 329


# ---- problem lines stay, flagged, and don't count --------------------------------------------

def test_problem_lines_stay_in_the_cart_and_are_not_counted(agent):  # clarified 2026-10-17
    s = Session()
    agent.handle(s, "brahma")
    t = agent.handle(s, "spaten+heineken")
    assert lines(t) == {"BRA-CHP-LT350": (36, "ok"), "SPA-LT350": (0, "unavailable"),
                        "heineken": (0, "unknown")}
    assert t.cart["total_cents"] == 36 * 329
    assert t.reply == "Spaten Can 350 ml is out of stock.\n\nWe don't carry “heineken”."
    t = agent.handle(s, "brahma 6")  # still there on later messages
    assert "SPA-LT350" in lines(t) and "heineken" in lines(t)
    t = agent.handle(s, "drop heineken")  # an unknown product can be removed by its words
    assert "heineken" not in lines(t)


def test_set_over_the_cap_flags_the_line_and_a_fix_restores_it(agent):  # R10
    s = Session()
    agent.handle(s, "brahma")
    t = agent.handle(s, "make brahma 500")
    assert lines(t) == {"BRA-CHP-LT350": (0, "over_cap")} and t.cart["total_cents"] == 0
    assert "limit is 100 cases" in t.reply
    t = agent.handle(s, "make brahma 2")
    assert lines(t) == {"BRA-CHP-LT350": (24, "ok")}


def test_short_line_keeps_its_note_and_is_counted(agent):  # R4
    t = agent.handle(Session(), "stella")  # 5 cases asked, 6 in stock → ok
    assert lines(t) == {"STE-LN330": (120, "ok")}
    s = Session()
    agent.handle(s, "stella")
    t = agent.handle(s, "stella")  # 10 asked, 6 in stock
    assert lines(t) == {"STE-LN330": (144, "short")}
    assert t.reply == ("Only 6 cases of Stella Artois Long Neck 330 ml in stock "
                       "(you asked for 10 cases), so the cart has 6 cases.")
    assert t.cart["total_cents"] == 144 * 589


# ---- clarify -----------------------------------------------------------------------------------

def test_ambiguous_add_asks_and_keeps_the_rest_of_the_cart(agent):  # R8
    s = Session()
    agent.handle(s, "skol")
    t = agent.handle(s, "two")
    assert t.status == "clarify" and s.asking
    assert t.reply.splitlines()[0] == "Which guaraná did you mean?"
    options = {ln.split(". ", 1)[1] for ln in t.reply.splitlines()[1:]}
    assert options == {"Guaraná Antarctica Can 350 ml", "Guaraná Antarctica 2 L"}
    assert lines(t) == {"SKL-LT350": (24, "ok")}  # unchanged while asking
    assert agent.place_order(s).status == "question_open"  # Place order refused meanwhile
    second = t.reply.splitlines()[2].split(". ", 1)[1]  # answer with an option number
    t = agent.handle(s, "2")
    assert t.status == "cart" and not s.asking
    assert {ln["name"] for ln in t.cart["lines"]} == {
        "Skol Can 350 ml", "Brahma Chopp Can 350 ml", second}


def test_two_unclear_answers_give_a_not_added_line(agent):
    s = Session()
    agent.handle(s, "two")
    t = agent.handle(s, "dunno")
    assert t.status == "clarify" and t.reply.startswith("Sorry, I didn't get that.")
    t = agent.handle(s, "whatever")
    assert lines(t)["guaraná"] == (0, "not_added")
    assert lines(t)["BRA-CHP-LT350"] == (36, "ok")


def test_set_on_a_not_added_line_keeps_it_not_added(agent):  # M4 review note 2
    s = Session()
    agent.handle(s, "two")
    agent.handle(s, "dunno")
    agent.handle(s, "whatever")  # → guaraná not_added
    t = agent.handle(s, "make guarana 4")
    assert lines(t)["guaraná"] == (0, "not_added")
    assert "We don't carry" not in t.reply
    assert t.reply == "I couldn't tell which “guaraná” you meant, so I didn't add it."


def test_no_outside_a_question_does_not_clear_the_cart(agent):
    s = Session()
    agent.handle(s, "brahma")
    t = agent.handle(s, "no")  # goes to the parser like any message
    assert t.status == "cart" and "BRA-CHP-LT350" in lines(t)


def test_two_questions_answered_in_one_message(agent):
    s = Session()
    agent.handle(s, "twoamb")
    t = agent.handle(s, "2L and lata")
    assert set(lines(t)) == {"GUA-PET2L", "SKL-LT350"}


def test_bare_number_for_two_questions_asks_again(agent):  # Phase 3 clarified
    s = Session()
    agent.handle(s, "twoamb")
    assert agent.handle(s, "2").status == "clarify"
    t = agent.handle(s, "lata")  # a container word may answer both
    assert set(lines(t)) == {"GUA-LT350", "SKL-LT350"}


@pytest.mark.parametrize("word", ["cancel", "Never mind", "cancelar", "esquece",
                                  "no", "não", "stop", "deixa pra lá"])  # M4 review note 5
def test_cancel_inside_a_question_drops_only_the_open_product(agent, word):
    s = Session()
    agent.handle(s, "two")
    t = agent.handle(s, word)
    assert t.status == "cart" and not s.asking
    assert lines(t) == {"BRA-CHP-LT350": (36, "ok")}
    assert "OK, I left out “guaraná”." in t.reply
    assert agent.parser.calls == 1


# ---- place order and clear --------------------------------------------------------------------

def test_place_order_writes_once_deducts_stock_and_empties_the_cart(agent, conn):  # R15
    s = Session()
    agent.handle(s, "brahma")
    t = agent.place_order(s)
    assert t.status == "placed" and t.order_id == 1 and t.cart is None
    assert t.reply == ("✅ Order #1 placed: R$ 118.44. Thanks! Message me any time to start "
                       "a new one.")
    assert stock(conn, "BRA-CHP-LT350") == 600 - 36 and db.order_count(conn) == 1
    assert agent.cart(s) is None
    assert lines(agent.handle(s, "skol")) == {"SKL-LT350": (24, "ok")}  # a fresh cart


def test_place_order_after_stock_dropped_writes_nothing_and_reprices(agent, conn):  # R15
    s = Session()
    agent.handle(s, "stella")  # 5 of 6 cases
    conn.execute("UPDATE products SET stock = 48 WHERE sku = 'STE-LN330'")  # another buyer
    t = agent.place_order(s)
    assert t.status == "stock_changed" and db.order_count(conn) == 0
    assert lines(t) == {"STE-LN330": (48, "short")}
    assert agent.place_order(s).status == "placed"
    assert stock(conn, "STE-LN330") == 0


def test_place_order_with_nothing_orderable_writes_nothing(agent, conn):
    s = Session()
    assert agent.place_order(s).status == "nothing_to_order"
    agent.handle(s, "spaten+heineken")
    assert agent.place_order(s).status == "nothing_to_order"
    assert db.order_count(conn) == 0


@pytest.mark.parametrize("how", ["button", "clear cart", "start over", "cancel"])
def test_clear_empties_the_cart_without_llm_or_write(agent, conn, how):
    s = Session()
    agent.handle(s, "brahma")
    t = agent.clear(s) if how == "button" else agent.handle(s, how)
    assert t.status == "cleared" and t.cart is None and t.reply == "Cart cleared."
    assert agent.parser.calls == 1 and db.order_count(conn) == 0


def test_clear_button_during_a_question_clears_everything(agent):
    s = Session()
    agent.handle(s, "skol")
    agent.handle(s, "two")
    t = agent.clear(s)
    assert t.status == "cleared" and not s.asking and agent.cart(s) is None
    assert lines(agent.handle(s, "brahma")) == {"BRA-CHP-LT350": (36, "ok")}


def test_typing_place_the_order_never_commits(agent, conn):
    s = Session()
    agent.handle(s, "brahma")
    t = agent.handle(s, "empty")  # what the parser returns for "ok place it"
    assert t.status == "no_items" and "tap Place order" in t.reply
    assert db.order_count(conn) == 0 and lines(t) == {"BRA-CHP-LT350": (36, "ok")}


# ---- English, refusals, failures, isolation ---------------------------------------------------

def test_product_names_and_pack_words_are_english():
    names = " ".join(p["name"] for p in CATALOG)
    for pt in ("Lata", "Latão", "Litrão", "Garrafa", "Laranja", "Limão", "sem Gás"):
        assert pt not in names


def test_refusals_leave_the_cart_untouched(agent, conn):  # R13
    s = Session()
    before = lines(agent.handle(s, "brahma"))
    for msg in ("poem", "leak"):
        t = agent.handle(s, msg)
        assert t.status == "refused" and lines(t) == before and t.reply.startswith("Sorry")
    assert db.order_count(conn) == 0


def test_r13_items_win_over_refusal_intent(agent):
    t = agent.handle(Session(), "mixed_leak")
    assert t.status == "cart" and lines(t) == {"SKL-LT350": (24, "ok")}


def test_parse_failure_and_missing_key(agent):
    s = Session()
    agent.handle(s, "brahma")
    t = agent.handle(s, "boom")
    assert t.status == "error" and lines(t) == {"BRA-CHP-LT350": (36, "ok")}
    assert agent.handle(Session(), "nokey").reply == "The language model isn't configured in this demo."


def test_sessions_do_not_share_a_cart_or_a_question(agent):
    a, b = Session(), Session()
    agent.handle(a, "brahma")
    agent.handle(b, "two")
    assert a.asking is False and b.asking is True
    assert lines(agent.handle(a, "skol")) == {"BRA-CHP-LT350": (36, "ok"), "SKL-LT350": (24, "ok")}
    assert agent.place_order(b).status == "question_open"
    assert agent.cart(b) is None
