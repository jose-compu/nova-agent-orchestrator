"""Integration test: real Nova 2 Lite tool selection call."""
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


def test_select_tool_returns_valid_choice():
    from tool_selection import select_tool, VALID_CHOICES
    choice = select_tool("What is the capital of France?")
    assert choice in VALID_CHOICES


def test_select_tool_web_search_style():
    from tool_selection import select_tool
    # May return web_search or answer depending on model
    choice = select_tool("What is the weather in Tokyo today?")
    assert choice in ("answer", "web_search", "plan_then_act")


def test_select_tool_simple_greeting():
    from tool_selection import select_tool
    choice = select_tool("Hello!")
    assert choice in ("answer", "web_search", "plan_then_act")
