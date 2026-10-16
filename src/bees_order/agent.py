"""OrderAgent: the one entry point the UI and the eval both drive (same code path).

Each order runs in its own LangGraph thread (`<session>:<n>`); a session is in one of three
modes: idle (next message starts a new order), clarify (next message answers the question),
approval (buttons only — typed messages get a reminder and no LLM call).
"""

import threading
import time
import uuid
from dataclasses import dataclass, field

from langgraph.types import Command

from bees_order import db, replies
from bees_order.graph import build_graph
from bees_order.guards import Limits
from bees_order.matching import normalize

CANCEL_WORDS = {"cancelar", "cancela", "cancel", "esquece", "deixa", "deixa pra la",
                "nao", "no", "stop", "start over", "recomecar"}


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    order_seq: int = 0
    mode: str = "idle"  # idle | clarify | approval
    language: str = "pt"  # last detected message language
    lang_override: str | None = None  # "pt" | "en" from the UI toggle; None = Auto (Phase 5)
    trace_len: int = 0
    llm_messages: int = 0  # messages that called the LLM (R16 session limit)

    @property
    def reply_lang(self) -> str:
        return self.lang_override or self.language

    @property
    def thread_id(self) -> str:
        return f"{self.id}:{self.order_seq}"


@dataclass
class Turn:
    reply: str
    status: str  # clarify | cart | approved | cancelled | refused | no_items | empty | error |
    #              not_configured | pending | no_pending | ignored
    cart: dict | None = None
    order_id: int | None = None
    trace: list[dict] = field(default_factory=list)
    llm: dict | None = None
    ms: float = 0.0
    lang: str = "pt"  # language the reply is written in


class OrderAgent:
    def __init__(self, conn, parser, lock=None, limits: Limits | None = None):
        self.conn = conn
        self.parser = parser
        self.limits = limits  # None = unlimited (eval, tests)
        self.lock = lock or threading.RLock()
        self.graph = build_graph(conn, parser, lock=self.lock)

    def _cfg(self, s: Session) -> dict:
        return {"configurable": {"thread_id": s.thread_id}}

    def handle(self, s: Session, text: str) -> Turn:
        t0 = time.perf_counter()
        text = (text or "").strip()
        if not text:
            return Turn(reply="", status="ignored")
        with self.lock:
            db.maybe_reset(self.conn)  # R17
        if s.mode == "approval":
            return Turn(reply=replies.t(s.reply_lang, "pending"), status="pending",
                        cart=self._state(s).get("cart"), ms=self._ms(t0), lang=s.reply_lang)
        if s.mode == "clarify" and normalize(text) in CANCEL_WORDS:
            s.mode = "idle"  # the interrupted thread is simply abandoned; nothing was written
            return Turn(reply=replies.t(s.reply_lang, "cancelled"), status="cancelled",
                        ms=self._ms(t0), lang=s.reply_lang)
        if s.mode == "clarify":
            result = self.graph.invoke(self._resume(s, text), self._cfg(s))
        else:
            if self.limits and s.llm_messages >= self.limits.session_limit:
                return Turn(reply=replies.limit_text(s.lang_override, "session",
                                                     n=self.limits.session_limit),
                            status="limit_session", ms=self._ms(t0), lang=s.reply_lang)
            if self.limits and self.limits.daily.exhausted():
                return Turn(reply=replies.limit_text(s.lang_override, "daily"),
                            status="limit_daily", ms=self._ms(t0), lang=s.reply_lang)
            s.order_seq += 1
            s.trace_len = 0
            result = self.graph.invoke({"message": text, "trace": [], "status": None,
                                        "lang_override": s.lang_override}, self._cfg(s))
        return self._turn(s, result, t0)

    def approve(self, s: Session) -> Turn:
        return self._decide(s, "approve")

    def cancel(self, s: Session) -> Turn:
        return self._decide(s, "cancel")

    def _decide(self, s: Session, decision: str) -> Turn:
        t0 = time.perf_counter()
        if s.mode != "approval":
            return Turn(reply="", status="no_pending")
        result = self.graph.invoke(self._resume(s, decision), self._cfg(s))
        return self._turn(s, result, t0)

    @staticmethod
    def _resume(s: Session, value: str) -> Command:
        # Carry the current toggle into the paused thread, so a switch mid-order applies
        # to the very next reply (Phase 5).
        return Command(resume=value, update={"lang_override": s.lang_override})

    def _state(self, s: Session) -> dict:
        return self.graph.get_state(self._cfg(s)).values

    @staticmethod
    def _ms(t0: float) -> float:
        return round((time.perf_counter() - t0) * 1000, 1)

    def _turn(self, s: Session, result: dict, t0: float) -> Turn:
        trace = result.get("trace", [])
        new_trace, s.trace_len = trace[s.trace_len:], len(trace)
        s.language = result.get("language") or s.language
        interrupts = result.get("__interrupt__") or []
        called_llm = any(st["step"] == "parse" for st in new_trace)
        llm = result.get("llm") if called_llm else None
        if called_llm:
            s.llm_messages += 1
        if interrupts:
            payload = interrupts[0].value
            s.mode = "clarify" if payload["kind"] == "clarify" else "approval"
            status = "clarify" if s.mode == "clarify" else "cart"
            return Turn(reply=payload["reply"], status=status, cart=result.get("cart"),
                        trace=new_trace, llm=llm, ms=self._ms(t0), lang=s.reply_lang)
        s.mode = "idle"
        return Turn(reply=result.get("reply", ""), status=result.get("status") or "error",
                    cart=result.get("cart"), order_id=result.get("order_id"),
                    trace=new_trace, llm=llm, ms=self._ms(t0), lang=s.reply_lang)
