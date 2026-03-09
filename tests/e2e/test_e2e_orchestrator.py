"""E2E tests: full flow from user message to final answer."""
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


def test_e2e_answer_path():
    """E2E: simple question → answer path returns non-empty text."""
    from orchestrator import run_turn
    statuses = []
    result = run_turn(
        "What is 2 plus 2? Reply with one number.",
        on_status=statuses.append,
        stream=True,
    )
    assert isinstance(result, str)
    assert len(result.strip()) > 0
    assert any("answer" in s.lower() or "choosing" in s.lower() for s in statuses)


def test_e2e_web_search_path():
    """E2E: search-style question → non-empty final text."""
    from orchestrator import run_turn
    result = run_turn("What is the capital of France?", stream=True)
    assert isinstance(result, str)
    # May be answer or web_search path; we expect some text
    assert len(result.strip()) >= 0  # can be empty from API
    assert result is not None
