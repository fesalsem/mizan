"""
Shared pytest setup.

server.py calls check_setup() at import time and exits with status 1 when
TIINGO_API_KEY is unset, and it builds the Flask app at module level. The
environment therefore has to be prepared *before* the module is imported.

The key used here is a placeholder. It is never sent anywhere: every test that
would reach the provider replaces fetch_stock, tiingo_get or fetch_bursa_live
with a stub, so the suite runs fully offline with no credentials.
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Non-empty so check_setup() does not sys.exit(1) during the import.
if not os.environ.get("TIINGO_API_KEY"):
    os.environ["TIINGO_API_KEY"] = "test-key-not-real"

# Read once at import by server.py. The cache-endpoint tests cover the
# "no token configured" path, so make that state deterministic rather than
# inheriting whatever the developer's shell happens to export.
os.environ.pop("MIZAN_ADMIN_TOKEN", None)

# Make the repository root importable so `import server` resolves regardless of
# how pytest is invoked.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import server as _server  # noqa: E402  (the environment must be set first)


@pytest.fixture(scope="session")
def srv():
    """The one imported server module, shared by every test."""
    return _server


class _NetworkBlocked(BaseException):
    """Deliberately not an Exception: server.py wraps provider calls in
    `except Exception`, so an Exception here could be swallowed. This one
    propagates and fails the test."""


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch):
    """Fail loudly if any test reaches for a real provider call."""
    def _blocked(*args, **kwargs):
        raise _NetworkBlocked(
            "A test attempted a real network call; the suite must run offline."
        )

    monkeypatch.setattr(_server.requests, "get", _blocked)
