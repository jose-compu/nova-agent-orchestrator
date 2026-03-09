"""Integration tests for nova_client wrapper."""
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


def test_nova_client_chat():
    from nova_client import chat, get_client
    client = get_client()
    r = client.chat.completions.create(
        model="nova-2-lite-v1",
        messages=[{"role": "user", "content": "Say ok"}],
        max_tokens=5,
    )
    assert r.choices and r.choices[0].message.content


def test_nova_client_chat_helper():
    from nova_client import chat
    r = chat(
        messages=[{"role": "user", "content": "Reply with one word: yes"}],
        max_tokens=5,
    )
    assert r.choices and r.choices[0].message.content


def test_nova_client_stream_chat():
    from nova_client import stream_chat
    parts = list(stream_chat(
        messages=[{"role": "user", "content": "Say hi"}],
        max_tokens=5,
    ))
    assert len(parts) >= 1
    assert "".join(parts).strip()


@pytest.mark.asyncio
async def test_nova_client_realtime_tts():
    from nova_client import realtime_tts
    audio, transcript = await realtime_tts("Say hello in one word.")
    assert audio or transcript is not None
