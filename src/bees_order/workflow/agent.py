"""OrderAgent: the one entry point that both the UI and the eval drive (same code path).

A Session is one browser tab. It owns one graph thread, which keeps its draft cart. The
agent adds what the graph can't know: the demo's rate limits (R16), the lazy stock reset
(R17), whether a question is waiting for an answer, and the chat history, which every
message passes into the graph for the parse call.
"""

import os
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from langgraph.types import Command

from bees_order.domain.matching import normalize
from bees_order.services import db
from bees_order.ui.replies import t
from bees_order.workflow.graph import CANCEL_WORDS, build_graph


class DailyCallCap:
    """R16: OpenRouter API calls per UTC day, across all sessions. The parser's on_call hook
    records every call; the US$10 cap on the key is the last line behind this."""

    def __init__(self, cap: int | None = None, today: Callable[[], date] | None = None):
        self.cap = cap if cap is not None else int(os.getenv("DAILY_LLM_CALL_CAP", "2000"))
        self._today = today or (lambda: datetime.now(UTC).date())
        self._lock = threading.Lock()
        self._day, self.count = self._today(), 0

    def _roll(self) -> None:
        if self._today() != self._day:
            self._day, self.count = self._today(), 0

    def record(self) -> None:
        with self._lock:
            self._roll()
            self.count += 1

    def exhausted(self) -> bool:
        with self._lock:
            self._roll()
            return self.count >= self.cap


@dataclass
class Limits:
    """R16: LLM messages per session (answers and buttons are free), plus the daily cap."""

    session_limit: int = field(default_factory=lambda: int(os.getenv("SESSION_MESSAGE_LIMIT",
                                                                       "40")))
    daily: DailyCallCap = field(default_factory=DailyCallCap)


HISTORY_EXCHANGES = 100  # the parse call sees at most this many earlier exchanges

# Values that belong to one call only; reset at the start of every new call.
_FRESH = {"status": None, "reply": "", "edits": [], "order_id": None, "llm": None}


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    asking: bool = False  # a clarify question is waiting for the next message
    llm_messages: int = 0  # messages that called the LLM (R16 session limit)
    # (customer message, reply) pairs, oldest first. Kept here, not in the graph thread,
    # because writing to a paused thread would disturb its open question.
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_EXCHANGES))


@dataclass
class Turn:
    reply: str
    status: str  # cart | clarify | answered | refused | no_items | error | not_configured | placed |
    #              stock_changed | nothing_to_order | cleared | question_open |
    #              limit_session | limit_daily | ignored
    cart: dict | None = None  # the cart after this turn (None = empty)
    order_id: int | None = None
    llm: dict | None = None  # model and cost, when this turn called the LLM
    ms: float = 0.0


class OrderAgent:
    def __init__(self, conn, parser, lock=None, limits: Limits | None = None):
        self.conn = conn
        self.parser = parser
        self.limits = limits  # None = unlimited (eval, tests)
        self.lock = lock or threading.RLock()
        self.graph = build_graph(conn, parser, lock=self.lock)

    def handle(self, s: Session, text: str) -> Turn:
        """A typed chat message."""
        text = (text or "").strip()
        if not text:
            return Turn(reply="", status="ignored", cart=self.cart(s))
        self._maybe_reset()
        if s.asking:  # the message answers the open question (no LLM call)
            return self._run(s, Command(resume=text), said=text)
        if normalize(text) in CANCEL_WORDS:
            return self.clear(s, said=text)
        if self.limits and s.llm_messages >= self.limits.session_limit:
            return self._said(s, text, Turn(reply=t("limit_session", n=self.limits.session_limit),
                                            status="limit_session", cart=self.cart(s)))
        if self.limits and self.limits.daily.exhausted():
            return self._said(s, text, Turn(reply=t("limit_daily"), status="limit_daily",
                                            cart=self.cart(s)))
        return self._run(s, {**_FRESH, "action": "message", "message": text,
                             "history": [list(x) for x in s.history]}, said=text)

    def place_order(self, s: Session) -> Turn:
        """The Place order button. Typing never places an order."""
        said = "(tapped Place order)"
        if s.asking:
            return self._said(s, said, Turn(reply=t("question_open"), status="question_open",
                                            cart=self.cart(s)))
        self._maybe_reset()
        return self._run(s, {**_FRESH, "action": "place_order"}, said=said)

    def clear(self, s: Session, said: str = "(tapped Clear cart)") -> Turn:
        """The Clear cart button, or "clear cart" typed. Also drops an open question."""
        return self._run(s, {**_FRESH, "action": "clear"}, said=said)

    def cart(self, s: Session) -> dict | None:
        return self.graph.get_state(self._config(s)).values.get("cart")

    def _run(self, s: Session, graph_input, said: str) -> Turn:
        t0 = time.perf_counter()
        # run_name labels the trace in LangSmith: message, place_order, clear or answer.
        name = graph_input["action"] if isinstance(graph_input, dict) else "answer"
        result = self.graph.invoke(graph_input, {**self._config(s), "run_name": name})
        ms = round((time.perf_counter() - t0) * 1000, 1)
        called_llm = isinstance(graph_input, dict) and result.get("llm") is not None
        if called_llm:
            s.llm_messages += 1
        llm = result.get("llm") if called_llm else None
        question = result.get("__interrupt__")
        s.asking = bool(question)
        if question:
            turn = Turn(reply=question[0].value["question"], status="clarify",
                        cart=result.get("cart"), llm=llm, ms=ms)
        else:
            turn = Turn(reply=result.get("reply", ""), status=result.get("status") or "error",
                        cart=result.get("cart"), order_id=result.get("order_id"), llm=llm,
                        ms=ms)
        return self._said(s, said, turn)

    @staticmethod
    def _said(s: Session, said: str, turn: Turn) -> Turn:
        """Record one exchange in the session's chat history."""
        s.history.append((said, turn.reply))
        return turn

    def _maybe_reset(self) -> None:
        with self.lock:
            db.maybe_reset(self.conn)  # R17

    @staticmethod
    def _config(s: Session) -> dict:
        return {"configurable": {"thread_id": s.id}}
