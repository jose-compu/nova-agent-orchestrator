"""Unit tests for tool selection parsing and message building."""
import pytest

from tool_selection import parse_tool_response, VALID_CHOICES


def test_parse_exact_answer():
    assert parse_tool_response("answer") == "answer"
    assert parse_tool_response("  answer  ") == "answer"


def test_parse_exact_web_search():
    assert parse_tool_response("web_search") == "web_search"
    assert parse_tool_response("web_search\n") == "web_search"


def test_parse_exact_plan_then_act():
    assert parse_tool_response("plan_then_act") == "plan_then_act"


def test_parse_first_word():
    assert parse_tool_response("answer and something") == "answer"
    assert parse_tool_response("web_search please") == "web_search"


def test_parse_fallback_search():
    assert parse_tool_response("search") == "web_search"
    assert parse_tool_response("I will do web search") == "web_search"


def test_parse_fallback_plan():
    assert parse_tool_response("plan") == "plan_then_act"
    assert parse_tool_response("plan_then_act is best") == "plan_then_act"


def test_parse_empty_or_garbage_defaults_answer():
    assert parse_tool_response("") == "answer"
    assert parse_tool_response("   ") == "answer"
    assert parse_tool_response("hello") == "answer"
    assert parse_tool_response("unknown") == "answer"


def test_valid_choices():
    assert VALID_CHOICES == frozenset({"answer", "web_search", "plan_then_act", "store_memory"})


def test_parse_store_memory():
    assert parse_tool_response("store_memory") == "store_memory"
    assert parse_tool_response("memory") == "store_memory"
    assert parse_tool_response("store") == "store_memory"
