"""
Nova API client: Chat (Nova 2 Lite) and Realtime (Nova 2 Sonic).
"""
import os
from typing import Any, Iterator

from openai import APIError, OpenAI, RateLimitError

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

NOVA_BASE = "https://api.nova.amazon.com/v1"
MODEL_LITE = "nova-2-lite-v1"
MODEL_SONIC = "nova-2-sonic-v1"


def get_client(api_key: str | None = None) -> OpenAI:
    key = api_key or os.environ.get("NOVA_API_KEY")
    if not key:
        raise ValueError("NOVA_API_KEY not set")
    return OpenAI(api_key=key, base_url=NOVA_BASE, timeout=60.0)


def chat(
    messages: list[dict[str, Any]],
    model: str = MODEL_LITE,
    max_tokens: int = 1024,
    stream: bool = False,
    tools: list[dict] | None = None,
    tool_choice: str = "auto",
    system_tools: list[str] | None = None,
    reasoning_effort: str | None = None,
    api_key: str | None = None,
):
    """Chat completion with Nova 2 Lite (or other model)."""
    client = get_client(api_key)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if tools is not None:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice
    if system_tools:
        kwargs["extra_body"] = {"system_tools": system_tools}
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    return client.chat.completions.create(**kwargs)


def stream_chat(
    messages: list[dict[str, Any]],
    model: str = MODEL_LITE,
    max_tokens: int = 1024,
    system_tools: list[str] | None = None,
    api_key: str | None = None,
) -> Iterator[str]:
    """Stream chat completion; yield content deltas."""
    resp = chat(
        messages=messages,
        model=model,
        max_tokens=max_tokens,
        stream=True,
        system_tools=system_tools,
        api_key=api_key,
    )
    for chunk in resp:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


async def realtime_tts(
    text: str,
    voice: str = "olivia",
    instructions: str = "Respond briefly.",
    api_key: str | None = None,
) -> tuple[bytes, str | None]:
    """
    Send text to Nova 2 Sonic Realtime; return (pcm_audio_bytes, transcript).
    PCM 16-bit mono 24kHz.
    """
    import asyncio
    import base64
    import json
    import ssl

    try:
        import websockets
    except ImportError as e:
        raise RuntimeError("pip install websockets") from e

    key = api_key or os.environ.get("NOVA_API_KEY")
    if not key:
        raise ValueError("NOVA_API_KEY not set")

    url = "wss://api.nova.amazon.com/v1/realtime?model=nova-2-sonic-v1"
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    headers = {"Authorization": f"Bearer {key}", "Origin": "https://api.nova.amazon.com"}

    SAMPLE_RATE = 24000
    CHANNELS = 1
    SAMPLE_WIDTH = 2

    def silence(duration_ms: int) -> bytes:
        n = (SAMPLE_RATE * duration_ms) // 1000 * SAMPLE_WIDTH * CHANNELS
        return bytes(n)

    async with websockets.connect(url, ssl=ssl_ctx, additional_headers=headers) as ws:
        ev = json.loads(await ws.recv())
        if ev.get("type") != "session.created":
            raise RuntimeError(f"Expected session.created: {ev}")

        await ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": instructions,
                "audio": {
                    "input": {"turn_detection": {"threshold": 0.5}},
                    "output": {"voice": voice},
                },
            },
        }))
        ev = json.loads(await ws.recv())
        if ev.get("type") != "session.updated":
            raise RuntimeError(f"Expected session.updated: {ev}")

        async def send_silence():
            b64 = base64.b64encode(silence(100)).decode("utf-8")
            try:
                while True:
                    await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": b64}))
                    await asyncio.sleep(0.1)
            except Exception:
                pass

        silence_task = asyncio.create_task(send_silence())
        try:
            await ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }))

            audio_chunks: list[bytes] = []
            transcript: str | None = None
            deadline = asyncio.get_event_loop().time() + 20.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                ev = json.loads(msg)
                t = ev.get("type")
                if t == "error":
                    raise RuntimeError(ev.get("error", ev))
                if t == "response.output_audio.delta":
                    audio_chunks.append(base64.b64decode(ev["delta"]))
                if t == "response.output_audio_transcript.done":
                    transcript = ev.get("transcript") or ""
                if t == "response.done":
                    break
            return (b"".join(audio_chunks), transcript)
        finally:
            silence_task.cancel()
            try:
                await silence_task
            except asyncio.CancelledError:
                pass
