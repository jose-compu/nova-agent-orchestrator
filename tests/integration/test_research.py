"""Integration test for research (nova_grounding)."""
import os

import pytest

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

pytestmark = pytest.mark.skipif(
    not os.environ.get("NOVA_API_KEY"),
    reason="NOVA_API_KEY not set",
)


def test_research_returns_string():
    from research import research
    out = research("What is the capital of France?")
    assert isinstance(out, str)


def test_research_search_style():
    from research import research
    out = research("Weather in London today?", max_tokens=100)
    assert isinstance(out, str)
    # May be empty or contain grounded text
    assert out is not None
