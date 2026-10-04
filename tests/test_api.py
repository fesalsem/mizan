"""
Flask routes, driven through app.test_client().

Every route that would reach the provider has fetch_stock monkeypatched, so
these are offline tests. requests is never called.
"""
import pytest

import server as mizan


# A remote address that is not loopback, used for the endpoints whose
# unconfigured-token behaviour differs by caller.
NON_LOOPBACK = {"REMOTE_ADDR": "203.0.113.5"}


@pytest.fixture
def client(srv):
    return srv.app.test_client()


# ── /health ───────────────────────────────────────────────

def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


# ── /screen ───────────────────────────────────────────────

def test_screen_without_symbol_is_400(client):
    assert client.get("/screen").status_code == 400


@pytest.mark.parametrize("symbol", ["AA PL", "AA/PL", "ABCDEFGHIJKLM", "AA;PL"])
def test_screen_malformed_symbol_is_400_and_never_fetches(client, monkeypatch, symbol):
    calls = []

    def _spy(value):
        calls.append(value)
        return {}

    monkeypatch.setattr(mizan, "fetch_stock", _spy)
    response = client.get("/screen", query_string={"symbol": symbol})
    assert response.status_code == 400
    assert calls == []


def test_screen_valid_symbol_returns_the_payload(client, monkeypatch):
    payload = {
        "ticker": "AAPL",
        "name": "Apple Inc.",
        "screening": {"verdict": "Potentially Halal"},
        "_cached": False,
    }
    monkeypatch.setattr(mizan, "fetch_stock", lambda _symbol: payload)

    response = client.get("/screen", query_string={"symbol": "AAPL"})
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["data"] == payload


def test_screen_upstream_error_is_502_not_400(client, monkeypatch):
    # Regression: an upstream outage used to be answered with 400 and the raw
    # provider message, blaming the caller for the provider's problem.
    def _boom(_symbol):
        raise mizan.UpstreamError("The data provider could not be reached.")

    monkeypatch.setattr(mizan, "fetch_stock", _boom)
    response = client.get("/screen", query_string={"symbol": "AAPL"})
    assert response.status_code == 502


def test_screen_value_error_is_400(client, monkeypatch):
    def _bad_symbol(_symbol):
        raise ValueError("Unknown symbol")

    monkeypatch.setattr(mizan, "fetch_stock", _bad_symbol)
    response = client.get("/screen", query_string={"symbol": "AAPL"})
    assert response.status_code == 400


# ── /purify ───────────────────────────────────────────────

def test_purify_valid_parameters_do_the_arithmetic(client):
    response = client.get(
        "/purify", query_string={"dividend": 100, "interest_ratio": 0.10}
    )
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["purifyAmount"] == pytest.approx(10.0)
    assert data["keepAmount"] == pytest.approx(90.0)


@pytest.mark.parametrize(
    "params",
    [
        {"dividend": -1, "interest_ratio": 0.1},
        {"dividend": 100, "interest_ratio": -0.1},
        {"dividend": 100, "interest_ratio": 1.5},
    ],
)
def test_purify_rejects_out_of_range_parameters(client, params):
    assert client.get("/purify", query_string=params).status_code == 400


def test_purify_rejects_infinite_dividend_without_raising(client):
    response = client.get(
        "/purify", query_string={"dividend": "inf", "interest_ratio": 0.1}
    )
    assert response.status_code == 400


# ── /cache (unauthenticated access guard) ─────────────────

def test_cache_clear_post_without_token_from_non_loopback_is_403(client):
    response = client.post("/cache/clear", environ_base=NON_LOOPBACK)
    assert response.status_code == 403


def test_cache_clear_get_is_405(client):
    # Regression: this was a GET any crawler or prefetcher could trigger, which
    # cleared the cache and made the next visitor pay the provider quota again.
    assert client.get("/cache/clear").status_code == 405


def test_cache_stats_without_token_is_403(client):
    response = client.get("/cache/stats", environ_base=NON_LOOPBACK)
    assert response.status_code == 403
