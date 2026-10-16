"""Phase 2 criterion: parse returns a valid ParsedOrder, retries once on invalid output,
falls back to the second model on an API error, and fails cleanly. No network: fake client."""

import json
from types import SimpleNamespace

import pytest

from bees_order import llm
from bees_order.llm import NotConfigured, OpenRouterParser, ParseError

GOOD = json.dumps({"intent": "order", "language": "pt",
                   "items": [{"product_text": "brahma lata", "quantity": 3, "unit": "pack"}]})


class FakeClient:
    def __init__(self, script):
        self.script = list(script)  # each entry: str content, or Exception to raise
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=nxt))],
            usage=SimpleNamespace(model_dump=lambda: {"cost": 0.0001, "prompt_tokens": 900,
                                                      "completion_tokens": 40}))


@pytest.fixture(autouse=True)
def spend_log(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "SPEND_LOG", tmp_path / "spend.jsonl")
    return tmp_path / "spend.jsonl"


def test_valid_output_parsed_and_spend_logged(spend_log):
    client = FakeClient([GOOD])
    r = OpenRouterParser(model="m1", fallback="", client=client)("manda 3 cx brahma lata")
    assert r.parsed.items[0].product_text == "brahma lata"
    assert (r.model, r.attempts) == ("m1", 1)
    call = client.calls[0]
    assert call["response_format"]["json_schema"]["strict"] is True
    assert call["extra_body"]["reasoning"] == {"enabled": False}
    assert len(spend_log.read_text().splitlines()) == 1


def test_invalid_output_retried_once_with_error():
    client = FakeClient(["not json", GOOD])
    r = OpenRouterParser(model="m1", fallback="", client=client)("x")
    assert r.attempts == 2
    retry_messages = client.calls[1]["messages"]
    assert retry_messages[-1]["role"] == "user" and "not valid JSON" in retry_messages[-1]["content"]


def test_garbage_twice_fails_cleanly():
    client = FakeClient(["garbage", '{"intent": "order"}x'])
    with pytest.raises(ParseError):
        OpenRouterParser(model="m1", fallback="", client=client)("x")


def test_api_error_falls_back_to_second_model():
    client = FakeClient([TimeoutError("slow"), GOOD])
    r = OpenRouterParser(model="m1", fallback="m2", client=client)("x")
    assert r.model == "m2"
    assert [c["model"] for c in client.calls] == ["m1", "m2"]


def test_not_configured_without_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(NotConfigured):
        OpenRouterParser(model="m1", fallback="", api_key="")("x")


def test_prompt_never_contains_catalog_or_prices():
    assert "R$" not in llm.SYSTEM_PROMPT
    assert "BRA-" not in llm.SYSTEM_PROMPT


def test_bad_output_never_moves_to_fallback():  # §3: one call + at most one retry, total
    client = FakeClient(["garbage", "garbage"])
    with pytest.raises(ParseError):
        OpenRouterParser(model="m1", fallback="m2", client=client)("x")
    assert [c["model"] for c in client.calls] == ["m1", "m1"]


def test_timeout_then_bad_output_uses_the_single_retry_on_fallback():
    client = FakeClient([TimeoutError("slow"), "garbage", "garbage"])
    with pytest.raises(ParseError):
        OpenRouterParser(model="m1", fallback="m2", client=client)("x")
    assert [c["model"] for c in client.calls] == ["m1", "m2", "m2"]


def test_both_models_down_fails_after_two_calls():
    client = FakeClient([TimeoutError("a"), ConnectionError("b")])
    with pytest.raises(ParseError):
        OpenRouterParser(model="m1", fallback="m2", client=client)("x")
    assert len(client.calls) == 2


def test_fallback_retry_does_not_carry_primary_messages():
    client = FakeClient([TimeoutError("slow"), GOOD])
    OpenRouterParser(model="m1", fallback="m2", client=client)("x")
    assert len(client.calls[1]["messages"]) == 2
