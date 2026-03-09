"""
Pre-implementation validation: API key, Nova 2 Lite, Nova 2 Sonic.
Run with: pytest tests/integration/test_nova_api_validation.py -v
Requires: NOVA_API_KEY in env or .env
"""
import asyncio
import base64
import json
import os
import ssl

import pytest

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from openai import OpenAI

pytestmark = pytest.mark.skipif(
    not os.environ.get("NOVA_API_KEY"),
    reason="NOVA_API_KEY not set",
)

NOVA_BASE = "https://api.nova.amazon.com/v1"
MODEL_LITE = "nova-2-lite-v1"
MODEL_SONIC = "nova-2-sonic-v1"
REALTIME_WS = "wss://api.nova.amazon.com/v1/realtime?model=nova-2-sonic-v1"
SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2


def _client() -> OpenAI:
    key = os.environ.get("NOVA_API_KEY")
    if not key:
        raise RuntimeError("NOVA_API_KEY not set")
    return OpenAI(api_key=key, base_url=NOVA_BASE)


# --- API key and connectivity ---


def test_models_list():
    """Verify API key and connectivity via minimal chat completion."""
    client = _client()
    resp = client.chat.completions.create(
        model=MODEL_LITE,
        messages=[{"role": "user", "content": "Reply with one word: ok"}],
        max_tokens=5,
    )
    assert resp.choices
    assert resp.choices[0].message.content


def test_nova_lite_simple_completion():
    """Nova 2 Lite: simple completion returns text."""
    client = _client()
    resp = client.chat.completions.create(
        model=MODEL_LITE,
        messages=[{"role": "user", "content": "Say hello in one word."}],
        max_tokens=10,
    )
    assert resp.choices
    assert resp.choices[0].message.content is not None
    assert len(resp.choices[0].message.content.strip()) > 0


def test_nova_lite_streaming():
    """Nova 2 Lite: stream=True returns at least one chunk with content."""
    client = _client()
    stream = client.chat.completions.create(
        model=MODEL_LITE,
        messages=[{"role": "user", "content": "Say hi."}],
        max_tokens=5,
        stream=True,
    )
    chunks = list(stream)
    assert len(chunks) >= 1
    has_content = any(
        getattr(c.choices[0].delta, "content", None)
        for c in chunks
        if c.choices
    )
    assert has_content


def test_nova_lite_tool_calling():
    """Nova 2 Lite: tools and tool_choice; expect tool_calls or direct answer."""
    client = _client()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_time",
                "description": "Get current time.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    resp = client.chat.completions.create(
        model=MODEL_LITE,
        messages=[{"role": "user", "content": "What time is it?"}],
        tools=tools,
        tool_choice="auto",
        max_tokens=50,
    )
    assert resp.choices
    msg = resp.choices[0].message
    # Either tool_calls or a text answer is acceptable
    if getattr(msg, "tool_calls", None):
        assert len(msg.tool_calls) >= 1
    else:
        assert msg.content is not None


def test_nova_lite_grounding():
    """Nova 2 Lite: system_tools nova_grounding returns a response."""
    from openai import RateLimitError
    client = _client()
    try:
        resp = client.chat.completions.create(
            model=MODEL_LITE,
            messages=[{"role": "user", "content": "What is the weather in Paris today?"}],
            max_tokens=100,
            extra_body={"system_tools": ["nova_grounding"]},
        )
    except RateLimitError:
        pytest.skip("Rate limit exceeded (429); try again later")
    assert resp.choices
    # content may be None or empty; API may return citations elsewhere
    assert hasattr(resp.choices[0].message, "content")


def test_nova_lite_reasoning_effort():
    """Nova 2 Lite: reasoning_effort low/medium completes without error."""
    client = _client()
    resp = client.chat.completions.create(
        model=MODEL_LITE,
        messages=[{"role": "user", "content": "What is 2+2? Reply with one number."}],
        max_tokens=50,
        reasoning_effort="low",
    )
    assert resp.choices
    # Completion without error; content/reasoning_content may be empty


# --- Nova 2 Sonic Realtime (async) ---


def _generate_silence(duration_ms: int) -> bytes:
    samples = (SAMPLE_RATE * duration_ms) // 1000
    return bytes(samples * SAMPLE_WIDTH * CHANNELS)


@pytest.mark.asyncio
async def test_nova_sonic_realtime_session_and_text_response():
    """Nova 2 Sonic: connect, session.update, text-in, receive audio/transcript."""
    try:
        import websockets
    except ImportError:
        pytest.skip("websockets not installed")
    api_key = os.environ.get("NOVA_API_KEY")
    if not api_key:
        pytest.skip("NOVA_API_KEY not set")

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Origin": "https://api.nova.amazon.com",
    }

    async with websockets.connect(
        REALTIME_WS, ssl=ssl_ctx, additional_headers=headers
    ) as ws:
        # session.created
        ev = json.loads(await ws.recv())
        assert ev.get("type") == "session.created"

        # session.update
        await ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": "Respond in one short word.",
                "audio": {
                    "input": {"turn_detection": {"threshold": 0.5}},
                    "output": {"voice": "olivia"},
                },
            },
        }))
        ev = json.loads(await ws.recv())
        assert ev.get("type") == "session.updated"

        # Text input
        await ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Say hello"}],
            },
        }))

        # Optional: keep connection alive with silence
        async def send_silence():
            silence_b64 = base64.b64encode(_generate_silence(100)).decode("utf-8")
            try:
                while True:
                    await ws.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": silence_b64,
                    }))
                    await asyncio.sleep(0.1)
            except Exception:
                pass

        silence_task = asyncio.create_task(send_silence())
        try:
            response_audio = []
            transcript = None
            timeout = 15.0
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                ev = json.loads(msg)
                t = ev.get("type")
                if t == "error":
                    pytest.fail(f"Realtime error: {ev.get('error', ev)}")
                if t == "response.output_audio.delta":
                    response_audio.append(base64.b64decode(ev["delta"]))
                if t == "response.output_audio_transcript.done":
                    transcript = ev.get("transcript", "")
                if t == "response.done":
                    break
            assert response_audio or transcript is not None, "Expected audio or transcript from Sonic"
        finally:
            silence_task.cancel()
            try:
                await silence_task
            except asyncio.CancelledError:
                pass
