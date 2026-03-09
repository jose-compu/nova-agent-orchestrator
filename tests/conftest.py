"""Shared pytest fixtures for Nova assistant tests."""
import os

import pytest


def _get_api_key() -> str | None:
    from dotenv import load_dotenv
    load_dotenv()
    return os.environ.get("NOVA_API_KEY") or None


@pytest.fixture(scope="session")
def nova_api_key() -> str | None:
    """NOVA_API_KEY from env (or .env). None if not set."""
    return _get_api_key()


@pytest.fixture(scope="session")
def require_nova_api_key(nova_api_key: str | None) -> str:
    """Raise if NOVA_API_KEY not set; otherwise return it."""
    if not nova_api_key or not nova_api_key.strip():
        pytest.skip("NOVA_API_KEY not set; set it to run integration/e2e tests")
    return nova_api_key


def skip_if_no_api_key():
    """Return a pytest.mark.skipif for missing NOVA_API_KEY."""
    key = _get_api_key()
    return pytest.mark.skipif(
        not key or not key.strip(),
        reason="NOVA_API_KEY not set",
    )
