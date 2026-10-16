"""Phase 5 criteria: the Auto · PT · EN reply-language toggle (Session.lang_override)."""

import pytest

from bees_order.agent import OrderAgent, Session
from bees_order.guards import DailyCallCap, Limits
from bees_order.llm import NotConfigured, ParseError, ParseResult
from bees_order.schemas import ParsedItem, ParsedOrder


def o(lang, *items):
    return ParsedOrder(intent="order", language=lang,
                       items=[ParsedItem(product_text=t, quantity=q, unit="pack")
                              for t, q in items])


TABLE = {
    "en_order": o("en", ("skol cans", 2)),
    "pt_order": o("pt", ("skol lata", 2)),
    "en_amb": o("en", ("guaraná", 1)),
    "pt_amb": o("pt", ("guaraná", 1)),
    "en_poem": ParsedOrder(intent="off_topic", language="en"),
    "pt_poem": ParsedOrder(intent="off_topic", language="pt"),
    "boom": ParseError("bad"),
    "nokey": NotConfigured("no key"),
}


def parser(msg):
    out = TABLE[msg]
    if isinstance(out, Exception):
        raise out
    return ParseResult(out, "stub", 1)


@pytest.fixture
def agent(conn):
    return OrderAgent(conn, parser)


def sess(override=None):
    s = Session()
    s.lang_override = override
    return s


def test_auto_follows_the_message_language_as_before(agent):  # previously-correct inputs
    assert "Your order" in agent.handle(sess(), "en_order").reply
    assert "Seu pedido" in agent.handle(sess(), "pt_order").reply
    assert agent.handle(sess(), "en_poem").reply.startswith("Sorry")
    assert agent.handle(sess(), "pt_poem").reply.startswith("Desculpe")


def test_forced_pt_wins_over_an_english_message(agent):
    assert "Seu pedido" in agent.handle(sess("pt"), "en_order").reply
    assert agent.handle(sess("pt"), "en_amb").reply.startswith("Qual opção")
    assert agent.handle(sess("pt"), "en_poem").reply.startswith("Desculpe")


def test_forced_en_wins_over_a_portuguese_message(agent):
    t = agent.handle(sess("en"), "pt_order")
    assert "Your order" in t.reply and t.lang == "en"
    assert "case" in t.reply  # pack words follow the language too
    assert agent.handle(sess("en"), "pt_amb").reply.startswith("Which one")
    assert agent.handle(sess("en"), "pt_poem").reply.startswith("Sorry")


@pytest.mark.parametrize(("override", "msg", "expected"), [
    ("en", "nokey", "The language model isn't configured"),
    ("en", "boom", "Sorry, I had trouble"),
    (None, "nokey", "O modelo de linguagem"),  # Auto: unchanged, PT
    (None, "boom", "Desculpe, tive um problema"),
])
def test_replies_without_a_parse_follow_the_toggle(agent, override, msg, expected):
    assert agent.handle(sess(override), msg).reply.startswith(expected)


def test_pending_reminder_cancel_and_decisions_follow_the_toggle(agent):
    s = sess("en")
    agent.handle(s, "pt_order")
    assert agent.handle(s, "anything").reply.startswith("You have an order waiting")
    assert agent.cancel(s).reply == "Order cancelled. Nothing was saved."
    s = sess("en")
    agent.handle(s, "pt_amb")
    assert agent.handle(s, "cancelar").reply == "Order cancelled. Nothing was saved."


def test_switching_mid_order_applies_to_the_next_reply(agent):
    s = sess(None)
    t = agent.handle(s, "pt_order")
    assert "Seu pedido" in t.reply
    s.lang_override = "en"  # user flips the toggle while the cart is pending
    t = agent.approve(s)
    assert t.reply.startswith("✅ Order") and t.lang == "en"


def test_switching_during_clarify_applies_to_the_cart(agent):
    s = sess("pt")
    agent.handle(s, "en_amb")
    s.lang_override = "en"
    t = agent.handle(s, "2L")
    assert "Your order" in t.reply


def test_switching_back_to_auto_restores_detection(agent):
    s = sess("en")
    agent.handle(s, "pt_poem")
    s.lang_override = None
    assert agent.handle(s, "pt_poem").reply.startswith("Desculpe")


def test_limit_replies_single_language_when_forced_bilingual_in_auto(conn):
    limits = Limits(session_limit=0, daily=DailyCallCap(99))
    agent = OrderAgent(conn, parser, limits=limits)
    en = agent.handle(sess("en"), "pt_order").reply
    assert en.startswith("You've reached") and "Você" not in en
    pt = agent.handle(sess("pt"), "en_order").reply
    assert pt.startswith("Você atingiu") and "You've" not in pt
    auto = agent.handle(sess(None), "en_order").reply
    assert "Você" in auto and "You've" in auto
    daily = OrderAgent(conn, parser, limits=Limits(session_limit=9, daily=DailyCallCap(0)))
    assert daily.handle(sess("en"), "pt_order").reply.startswith("The demo has reached")


def test_default_is_auto():
    assert Session().lang_override is None
