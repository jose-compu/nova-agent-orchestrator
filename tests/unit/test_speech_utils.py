"""Unit tests for speech_utils."""
import pytest
from speech_utils import text_for_speech, summarize_for_speech, _first_sentences


def test_strips_links():
    t = "Paris is nice (https://example.com) and warm."
    assert "https" not in text_for_speech(t, use_ai_summary=False)
    assert "Paris" in text_for_speech(t, use_ai_summary=False)


def test_strips_bold():
    t = "The **temperature** is **20°C**."
    assert "**" not in text_for_speech(t, use_ai_summary=False)


def test_first_sentences():
    t = "First sentence. Second sentence. Third sentence."
    out = text_for_speech(t, max_chars=500, use_ai_summary=False)
    assert "First sentence" in out
    assert "Second sentence" in out


def test_shortens():
    t = "A. B. C. D. E."
    out = text_for_speech(t, max_chars=10, use_ai_summary=False)
    assert len(out) <= 12


def test_summarize_for_speech_short_text():
    """Short text returned as-is (no API call)."""
    t = "Hi."
    assert summarize_for_speech(t) == "Hi."
