"""Phase 2 criteria for the LangGraph workflow, driven through OrderAgent with a stub parser."""

import pytest

from bees_order import db
from bees_order.agent import OrderAgent, Session
from bees_order.llm import NotConfigured, ParseError, ParseResult
from bees_order.schemas import ParsedItem, ParsedOrder


def order(*items, lang="pt"):
    return ParsedOrder(intent="order", language=lang,
                       items=[ParsedItem(product_text=t, quantity=q, unit=u) for t, q, u in items])


class StubParser:
    def __init__(self, table):
        self.table = table
        self.calls = 0

    def __call__(self, msg):
        self.calls += 1
        out = self.table[msg]
        if isinstance(out, Exception):
            raise out
        return ParseResult(out, "stub", 1)


TABLE = {
    "brahma": order(("brahma lata", 3, "pack")),
    "two": order(("brahma lata", 3, "pack"), ("guaraná", 2, "pack")),
    "twoamb": order(("guaraná", 2, "pack"), ("skol", 1, "pack")),
    "stella": order(("stella", 5, "pack")),
    "poem": ParsedOrder(intent="off_topic", language="en"),
    "leak": ParsedOrder(intent="data_request", language="en"),
    "empty": ParsedOrder(intent="order", language="pt"),
    "spaten": order(("spaten lata", 2, "pack")),
    "boom": ParseError("bad output twice"),
    "nokey": NotConfigured("no key"),
    "mixed_leak": ParsedOrder(intent="data_request", language="pt",
                              items=[ParsedItem(product_text="skol lata", quantity=2, unit="pack")]),
}


@pytest.fixture
def agent(conn):
    return OrderAgent(conn, StubParser(TABLE))


def stock(conn, sku):
    return conn.execute("SELECT stock FROM products WHERE sku = ?", (sku,)).fetchone()[0]


def test_order_cart_approve_deducts_stock(agent, conn):
    s = Session()
    t = agent.handle(s, "brahma")
    assert t.status == "cart" and s.mode == "approval"
    assert "R$ 118,44" in t.reply
    t = agent.approve(s)
    assert t.status == "approved" and t.order_id == 1
    assert stock(conn, "BRA-CHP-LT350") == 600 - 36
    assert s.mode == "idle"


def test_order_cart_cancel_writes_nothing(agent, conn):
    s = Session()
    agent.handle(s, "brahma")
    t = agent.cancel(s)
    assert t.status == "cancelled"
    assert db.order_count(conn) == 0 and stock(conn, "BRA-CHP-LT350") == 600


def test_ambiguous_clarify_then_cart(agent):
    s = Session()
    t = agent.handle(s, "two")
    assert t.status == "clarify" and s.mode == "clarify"
    assert "Guaraná Antarctica 2 L" in t.reply and t.cart is None
    t = agent.handle(s, "2L")
    assert t.status == "cart"
    skus = {ln["sku"] for ln in t.cart["lines"]}
    assert skus == {"BRA-CHP-LT350", "GUA-PET2L"}


def test_clarify_answer_by_option_number_makes_no_llm_call(agent):
    s = Session()
    t = agent.handle(s, "two")
    before = agent.parser.calls
    option_two = t.reply.splitlines()[2].split(". ", 1)[1]  # "2. <name>"
    t = agent.handle(s, "2")
    assert t.status == "cart"
    assert any(ln["name"] == option_two for ln in t.cart["lines"])
    assert agent.parser.calls == before


def test_two_ambiguous_lines_answered_in_one_message(agent):
    s = Session()
    agent.handle(s, "twoamb")
    t = agent.handle(s, "2L e lata")
    assert t.status == "cart"
    assert {ln["sku"] for ln in t.cart["lines"]} == {"GUA-PET2L", "SKL-LT350"}


def test_two_failed_answers_drop_the_line(agent):
    s = Session()
    agent.handle(s, "two")
    t = agent.handle(s, "sei la")
    assert t.status == "clarify" and "Responda com o número" in t.reply  # one re-ask
    t = agent.handle(s, "qualquer")
    assert t.status == "cart"
    flags = {ln["name"]: ln["flag"] for ln in t.cart["lines"]}
    assert flags["guaraná"] == "not_added"
    assert flags["Brahma Chopp Lata 350 ml"] == "ok"


def test_off_topic_and_data_requests_refused_without_side_effects(agent, conn):
    for msg in ("poem", "leak"):
        s = Session()
        t = agent.handle(s, msg)
        assert t.status == "refused" and t.cart is None and s.mode == "idle"
        assert "Sorry" in t.reply
    assert db.order_count(conn) == 0


def test_no_items_reply(agent):
    assert agent.handle(Session(), "empty").status == "no_items"


def test_parse_failure_apologises_with_no_cart(agent):
    t = agent.handle(Session(), "boom")
    assert t.status == "error" and t.cart is None


def test_not_configured_status(agent):
    assert agent.handle(Session(), "nokey").status == "not_configured"


def test_pending_cart_is_buttons_only(agent):
    s = Session()
    agent.handle(s, "brahma")
    before = agent.parser.calls
    t = agent.handle(s, "poem")
    assert t.status == "pending" and s.mode == "approval"
    assert agent.parser.calls == before
    assert agent.approve(s).status == "approved"


def test_approve_without_pending_cart_is_noop(agent, conn):
    assert agent.approve(Session()).status == "no_pending"
    assert db.order_count(conn) == 0


def test_stock_changed_on_approve_rebuilds_cart(agent, conn):
    s = Session()
    agent.handle(s, "stella")  # 5 of 6 packs
    conn.execute("UPDATE products SET stock = 48 WHERE sku = 'STE-LN330'")  # another buyer
    t = agent.approve(s)
    assert t.status == "cart" and s.mode == "approval"
    line = t.cart["lines"][0]
    assert (line["flag"], line["packs"]) == ("short", 2)
    assert db.order_count(conn) == 0
    assert agent.approve(s).status == "approved"
    assert stock(conn, "STE-LN330") == 0


def test_nothing_billable_ends_without_approval(agent):
    s = Session()
    t = agent.handle(s, "spaten")
    assert t.status == "empty" and s.mode == "idle"


def test_trace_steps_in_order_with_timing(agent):
    t = agent.handle(Session(), "brahma")
    assert [st["step"] for st in t.trace] == ["parse", "match_stock", "build_cart"]
    assert all(st["ms"] >= 0 for st in t.trace)
    assert t.trace[1]["output"][0]["sku"] == "BRA-CHP-LT350"


def test_sessions_do_not_share_pending_state(agent):
    a, b = Session(), Session()
    agent.handle(a, "brahma")
    assert agent.handle(b, "poem").status == "refused"
    assert a.mode == "approval" and b.mode == "idle"
    assert agent.cancel(b).status == "no_pending"


def test_english_replies_follow_parsed_language(agent):
    t = agent.handle(Session(), "poem")
    assert t.reply.startswith("Sorry")


def test_r13_items_win_over_refusal_intent(agent, conn):
    t = agent.handle(Session(), "mixed_leak")
    assert t.status == "cart"
    assert [ln["sku"] for ln in t.cart["lines"]] == ["SKL-LT350"]


def test_every_node_appends_a_trace_step(agent):
    s = Session()
    agent.handle(s, "two")
    t = agent.handle(s, "2L")
    assert [st["step"] for st in t.trace] == ["clarify", "build_cart"]
    t = agent.approve(s)
    assert [st["step"] for st in t.trace] == ["await_approval", "commit"]
    assert t.trace[0]["output"] == {"decision": "approve"}


def test_bare_number_with_two_ambiguous_lines_reasks(agent):  # Phase 3 clarified
    s = Session()
    agent.handle(s, "twoamb")
    t = agent.handle(s, "2")
    assert t.status == "clarify" and "Responda com o número" in t.reply
    t = agent.handle(s, "lata, lata")
    assert {ln["sku"] for ln in t.cart["lines"]} == {"GUA-LT350", "SKL-LT350"}


def test_container_word_still_applies_to_every_line(agent):
    s = Session()
    agent.handle(s, "twoamb")
    t = agent.handle(s, "lata")
    assert {ln["sku"] for ln in t.cart["lines"]} == {"GUA-LT350", "SKL-LT350"}


@pytest.mark.parametrize("word", ["cancelar", "Cancel", "esquece", "deixa"])
def test_cancel_inside_clarify(agent, conn, word):  # Phase 3 clarified
    s = Session()
    agent.handle(s, "two")
    before = agent.parser.calls
    t = agent.handle(s, word)
    assert t.status == "cancelled" and s.mode == "idle"
    assert agent.parser.calls == before
    assert db.order_count(conn) == 0
    assert agent.handle(s, "brahma").status == "cart"  # next message is a new order
