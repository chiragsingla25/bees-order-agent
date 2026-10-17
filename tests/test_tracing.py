"""Phase 7 criterion (release-qa M4 note 1): LangSmith gets nothing unless both
LANGSMITH_TRACING and LANGSMITH_API_KEY are set. Checked end to end: a real graph runs in a
fresh Python process whose LangSmith endpoint is a local fake server, and we count what
arrives. Nothing leaves this machine."""

import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import sys; sys.path.insert(0, "src")
from bees_order.llm import require_key_for_tracing
require_key_for_tracing()  # what app.py does at start-up
from bees_order import db
from bees_order.agent import OrderAgent, Session
from bees_order.llm import ParseResult
from bees_order.schemas import ParsedItem, ParsedOrder
from langsmith import Client
stub = lambda msg, cart=(): ParseResult(ParsedOrder(intent="order", items=[
    ParsedItem(product_text="brahma lata", quantity=2, unit="pack")]), "stub", 1)
conn = db.init_db(":memory:"); db.seed(conn)
OrderAgent(conn, stub).handle(Session(), "customer text")
from langchain_core.tracers.langchain import wait_for_all_tracers
wait_for_all_tracers()
"""


class _Count(BaseHTTPRequestHandler):
    hits: ClassVar[list] = []

    def _ok(self):
        self.hits.append(self.path)
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    do_POST = do_PATCH = do_GET = _ok

    def log_message(self, *args):
        pass


def _posts_with(env: dict) -> list:
    _Count.hits = []
    server = HTTPServer(("127.0.0.1", 0), _Count)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = {k: v for k, v in __import__("os").environ.items()
                if not k.startswith(("LANGSMITH_", "LANGCHAIN_"))}
        endpoint = f"http://127.0.0.1:{server.server_port}"
        subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, check=True, timeout=60,
                       env={**base, "LANGSMITH_ENDPOINT": endpoint, **env})
    finally:
        server.shutdown()
    return [h for h in _Count.hits if "/runs" in h]


@pytest.mark.parametrize("env", [{}, {"LANGSMITH_TRACING": "true"},
                                 {"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": ""}])
def test_nothing_is_sent_without_both_vars(env):
    assert _posts_with(env) == []


def test_runs_are_sent_with_both_vars():  # the positive control: the fake endpoint works
    assert _posts_with({"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "lsv2-test"})
