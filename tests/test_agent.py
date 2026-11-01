"""The agent's tools and its loop.

Offline: _llm_chat is replaced with a fake that returns canned OpenAI-style
turns, and fetch_stock is stubbed wherever a screen would reach a provider.
"""
import json

import pytest

import server as mizan


@pytest.fixture
def client(srv):
    return srv.app.test_client()


# ── Name lookup ───────────────────────────────────────────

def test_find_company_resolves_common_names(srv):
    assert srv.find_company("maybank")[0]["code"] == "1155"
    assert srv.find_company("Maybank")[0]["code"] == "1155"
    assert srv.find_company("tenaga")[0]["code"] == "5347"
    assert srv.find_company("public bank")[0]["code"] == "1295"
    assert srv.find_company("nvidia")[0]["code"] == "NVDA"
    assert srv.find_company("NVDA")[0]["code"] == "NVDA"


def test_find_company_survives_a_typo(srv):
    codes = [r["code"] for r in srv.find_company("maybnk")]
    assert "1155" in codes


def test_find_company_returns_nothing_for_nonsense(srv):
    assert srv.find_company("xyzzy") == []


def test_list_companies_filters(srv):
    rows = srv.list_companies(sector="utilities")
    assert rows and all("utilities" in r["sector"].lower() for r in rows)
    assert all(r["market"] == "Bursa" for r in srv.list_companies(market="Bursa"))


# ── Screening tool ────────────────────────────────────────

def test_screen_company_refuses_symbols_outside_the_database(srv):
    out = srv.screen_company("FAKE")
    assert "error" in out


def test_screen_company_returns_the_engine_verdict(srv, monkeypatch):
    monkeypatch.setattr(mizan, "fetch_stock", lambda symbol: {
        "ticker": "1155", "name": "Malayan Banking Berhad (Maybank)",
        "sector": "Financial Services", "currency": "MYR", "price": 9.80,
        "debtRatio": 0.07, "interestRatio": None,
        "screening": {"verdict": "Potentially Halal", "vReason": "Passes.",
                      "risk": "LOW", "rec": "HOLD", "checks": [],
                      "missingInputs": [], "dataQuality": "complete"},
    })
    out = srv.screen_company("1155")
    assert out["verdict"] == "Potentially Halal"
    assert out["dataQuality"] == "complete"


# ── /ask ──────────────────────────────────────────────────

def test_ask_returns_502_when_the_model_is_unreachable(client, monkeypatch):
    def boom(messages, *_):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(mizan, "_llm_chat", boom)
    response = client.post("/ask", json={"question": "Is Maybank halal?"})
    assert response.status_code == 502
    assert response.get_json()["ok"] is False


def test_ask_without_question_is_400(client):
    assert client.post("/ask", json={}).status_code == 400
    assert client.post("/ask", json={"question": ""}).status_code == 400


def test_ask_rejects_long_questions(client):
    response = client.post("/ask", json={"question": "x" * 501})
    assert response.status_code == 400


def test_ask_endpoint_returns_the_agent_answer(client, monkeypatch):
    monkeypatch.setattr(mizan, "run_agent",
                        lambda q: {"answer": f"answer to {q}", "screened": []})
    response = client.post("/ask", json={"question": "is maybank halal?"})
    assert response.status_code == 200
    assert response.get_json()["data"]["answer"] == "answer to is maybank halal?"


# ── The model call ────────────────────────────────────────

def test_llm_chat_posts_an_openai_compatible_request(srv, monkeypatch):
    captured = {}

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(srv.requests, "post", fake_post)
    monkeypatch.setattr(srv, "LLM_BASE_URL", "http://example.test/v1")
    monkeypatch.setattr(srv, "LLM_API_KEY", "k")

    out = srv._llm_chat([{"role": "user", "content": "hello"}])

    assert out["content"] == "hi"
    assert captured["url"] == "http://example.test/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer k"
    assert captured["json"]["model"] == srv.AGENT_MODEL
    assert captured["json"]["tools"][0]["type"] == "function"
    assert captured["json"]["messages"][0]["role"] == "system"


# ── The loop ──────────────────────────────────────────────

def _tool_call(call_id, name, arguments):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


def test_agent_calls_a_tool_then_answers(srv, monkeypatch):
    turns = [
        {"role": "assistant", "content": None,
         "tool_calls": [_tool_call("t1", "find_company", {"query": "maybank"})]},
        {"role": "assistant", "content": "Maybank is Potentially Halal."},
    ]
    seen = []

    def fake_chat(messages, *_):
        seen.append([dict(m) for m in messages])
        return turns.pop(0)

    monkeypatch.setattr(srv, "_llm_chat", fake_chat)

    result = srv.run_agent("is maybank halal?")

    assert result["answer"] == "Maybank is Potentially Halal."
    tool_messages = [m for m in seen[1] if m.get("role") == "tool"]
    assert tool_messages and tool_messages[0]["tool_call_id"] == "t1"
    assert "1155" in tool_messages[0]["content"]


def test_agent_never_reports_an_unscreened_company(srv, monkeypatch):
    """A hallucinated ticker is refused by the tool and never lands in screened."""
    turns = [
        {"role": "assistant", "content": None,
         "tool_calls": [_tool_call("t1", "screen_company", {"symbol": "FAKE"})]},
        {"role": "assistant", "content": "I could not verify that."},
    ]
    seen = []

    def fake_chat(messages, *_):
        seen.append([dict(m) for m in messages])
        return turns.pop(0)

    monkeypatch.setattr(srv, "_llm_chat", fake_chat)

    result = srv.run_agent("is FAKE halal?")

    assert result["screened"] == []
    tool_messages = [m for m in seen[1] if m.get("role") == "tool"]
    assert tool_messages and '"error"' in tool_messages[0]["content"]


def test_agent_stops_after_the_round_limit(srv, monkeypatch):
    calls = []

    def fake_chat(messages, *_):
        calls.append(messages)
        return {"role": "assistant", "content": None,
                "tool_calls": [_tool_call(f"t{len(calls)}", "list_companies", {})]}

    monkeypatch.setattr(srv, "_llm_chat", fake_chat)

    result = srv.run_agent("keep going")

    assert "narrower question" in result["answer"]
    assert len(calls) == srv.AGENT_ROUNDS


def test_agent_gives_up_when_the_time_budget_is_spent(srv, monkeypatch):
    """A model that would hang is never called once the deadline has passed."""
    called = []
    monkeypatch.setattr(srv, "_llm_chat", lambda messages, *_: called.append(messages))
    monkeypatch.setattr(srv, "LLM_TIMEOUT", 0)

    result = srv.run_agent("is maybank halal?")

    assert called == []
    assert "narrower question" in result["answer"]
