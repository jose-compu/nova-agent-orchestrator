"""Unit tests for orchestrator message building."""
import pytest

from orchestrator import build_messages


def test_build_messages_user_only():
    msgs = build_messages("Hello")
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "Hello"


def test_build_messages_with_system():
    msgs = build_messages("Hi", system="You are helpful.")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "You are helpful."
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "Hi"


def test_build_messages_with_history():
    history = [
        {"role": "user", "content": "First"},
        {"role": "assistant", "content": "Reply"},
    ]
    msgs = build_messages("Second", history=history)
    assert len(msgs) == 3
    assert msgs[0]["role"] == "user" and msgs[0]["content"] == "First"
    assert msgs[1]["role"] == "assistant" and msgs[1]["content"] == "Reply"
    assert msgs[2]["role"] == "user" and msgs[2]["content"] == "Second"


def test_build_messages_system_history_user():
    msgs = build_messages(
        "Third",
        system="System",
        history=[
            {"role": "user", "content": "U1"},
            {"role": "assistant", "content": "A1"},
        ],
    )
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user" and msgs[1]["content"] == "U1"
    assert msgs[2]["role"] == "assistant" and msgs[2]["content"] == "A1"
    assert msgs[3]["role"] == "user" and msgs[3]["content"] == "Third"
