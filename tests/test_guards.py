"""Phase 3 criterion: the session limit (LLM messages only) and the daily API-call cap."""

from datetime import date

from bees_order.agent import OrderAgent, Session
from bees_order.guards import DailyCallCap, Limits
from bees_order.llm import ParseResult
from bees_order.schemas import ParsedItem, ParsedOrder


class Counting:
    def __init__(self, daily=None):
        self.calls = 0
        self.daily = daily

    def __call__(self, msg):
        self.calls += 1
        if self.daily:
            self.daily.record()
        if msg == "amb":
            items = [ParsedItem(product_text="guaraná", quantity=1, unit="pack")]
        else:
            items = [ParsedItem(product_text="skol lata", quantity=1, unit="pack")]
        return ParseResult(ParsedOrder(intent="order", language="pt", items=items), "stub", 1)


def test_21st_llm_message_is_refused_without_calling_the_llm(conn):
    parser = Counting()
    agent = OrderAgent(conn, parser, limits=Limits(session_limit=20, daily=DailyCallCap(10_000)))
    s = Session()
    for _ in range(20):
        assert agent.handle(s, "order").status == "cart"
        agent.cancel(s)
    t = agent.handle(s, "order")
    assert t.status == "limit_session" and "limite de 20" in t.reply
    assert parser.calls == 20
    assert agent.handle(Session(), "order").status == "cart"  # other sessions unaffected


def test_clarify_answers_and_buttons_do_not_count(conn):
    agent = OrderAgent(conn, Counting(), limits=Limits(session_limit=2, daily=DailyCallCap(99)))
    s = Session()
    agent.handle(s, "amb")
    agent.handle(s, "sei la")  # re-ask, free
    agent.handle(s, "2L")  # answer, free
    agent.handle(s, "typed while pending")  # reminder, free
    agent.approve(s)
    assert s.llm_messages == 1
    assert agent.handle(s, "order").status == "cart"


def test_daily_cap_blocks_every_session_and_rolls_over(conn):
    day = [date(2026, 10, 17)]
    daily = DailyCallCap(cap=3, today=lambda: day[0])
    parser = Counting(daily)
    agent = OrderAgent(conn, parser, limits=Limits(session_limit=100, daily=daily))
    for _ in range(3):
        s = Session()
        assert agent.handle(s, "order").status == "cart"
    t = agent.handle(Session(), "order")
    assert t.status == "limit_daily" and parser.calls == 3
    day[0] = date(2026, 10, 18)
    assert agent.handle(Session(), "order").status == "cart"


def test_empty_message_is_ignored(conn):
    parser = Counting()
    agent = OrderAgent(conn, parser, limits=Limits())
    assert agent.handle(Session(), "   ").status == "ignored"
    assert parser.calls == 0


def test_parser_on_call_hook_counts_each_api_request():
    from bees_order.llm import OpenRouterParser
    from tests.test_llm import GOOD, FakeClient
    daily = DailyCallCap(cap=10)
    client = FakeClient(["garbage", GOOD])
    OpenRouterParser(model="m1", fallback="", client=client, on_call=daily.record)("x")
    assert daily.count == 2
