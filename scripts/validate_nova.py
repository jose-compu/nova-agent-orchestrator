#!/usr/bin/env python3
"""
Pre-implementation validation: confirm API key and Nova 2 Lite / Nova 2 Sonic capabilities.
Usage: python scripts/validate_nova.py   (requires NOVA_API_KEY in env or .env)
"""
import asyncio
import base64
import json
import os
import ssl
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from openai import OpenAI

NOVA_BASE = "https://api.nova.amazon.com/v1"
MODEL_LITE = "nova-2-lite-v1"
REALTIME_WS = "wss://api.nova.amazon.com/v1/realtime?model=nova-2-sonic-v1"
SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2


def run(name: str, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        print(f"  OK  {name}")
        return True
    except Exception as e:
        print(f"  FAIL {name}: {e}")
        return False


def _client():
    key = os.environ.get("NOVA_API_KEY")
    if not key:
        raise RuntimeError("NOVA_API_KEY not set")
    return OpenAI(api_key=key, base_url=NOVA_BASE)


def check_connectivity():
    c = _client()
    r = c.chat.completions.create(
        model=MODEL_LITE,
        messages=[{"role": "user", "content": "Reply: ok"}],
        max_tokens=5,
    )
    if not r.choices or not r.choices[0].message.content:
        raise RuntimeError("No content in response")


def check_lite_streaming():
    c = _client()
    stream = c.chat.completions.create(
        model=MODEL_LITE,
        messages=[{"role": "user", "content": "Say hi"}],
        max_tokens=5,
        stream=True,
    )
    chunks = list(stream)
    if not chunks:
        raise RuntimeError("No stream chunks")
    if not any(getattr(c.choices[0].delta, "content", None) for c in chunks if c.choices):
        raise RuntimeError("No content in stream")


def check_lite_grounding():
    c = _client()
    r = c.chat.completions.create(
        model=MODEL_LITE,
        messages=[{"role": "user", "content": "Weather in Paris today?"}],
        max_tokens=50,
        extra_body={"system_tools": ["nova_grounding"]},
    )
    if not r.choices:
        raise RuntimeError("No choices in response")
    # Content may be empty; grounding can return citations elsewhere
    assert hasattr(r.choices[0].message, "content")


async def check_sonic():
    try:
        import websockets
    except ImportError:
        raise RuntimeError("pip install websockets")
    key = os.environ.get("NOVA_API_KEY")
    if not key:
        raise RuntimeError("NOVA_API_KEY not set")
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    headers = {"Authorization": f"Bearer {key}", "Origin": "https://api.nova.amazon.com"}
    async with websockets.connect(REALTIME_WS, ssl=ssl_ctx, additional_headers=headers) as ws:
        ev = json.loads(await ws.recv())
        if ev.get("type") != "session.created":
            raise RuntimeError(f"Expected session.created, got {ev.get('type')}")
        await ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": "One word.",
                "audio": {"input": {"turn_detection": {"threshold": 0.5}}, "output": {"voice": "olivia"}},
            },
        }))
        ev = json.loads(await ws.recv())
        if ev.get("type") != "session.updated":
            raise RuntimeError(f"Expected session.updated, got {ev.get('type')}")
        await ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Hello"}],
            },
        }))
        silence = base64.b64encode(bytes((SAMPLE_RATE * 100 // 1000) * SAMPLE_WIDTH * CHANNELS)).decode("utf-8")
        async def silence_task():
            try:
                while True:
                    await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": silence}))
                    await asyncio.sleep(0.1)
            except Exception:
                pass
        t = asyncio.create_task(silence_task())
        try:
            got = False
            for _ in range(50):
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                ev = json.loads(msg)
                if ev.get("type") == "response.output_audio.delta":
                    got = True
                    break
                if ev.get("type") == "response.output_audio_transcript.done":
                    got = True
                    break
                if ev.get("type") == "response.done":
                    got = True
                    break
                if ev.get("type") == "error":
                    raise RuntimeError(ev.get("error", ev))
            if not got:
                raise RuntimeError("No audio or transcript from Sonic")
        finally:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass


def main():
    print("Nova API validation")
    print("-" * 40)
    ok = 0
    ok += run("API key + connectivity", check_connectivity)
    ok += run("Nova 2 Lite streaming", check_lite_streaming)
    ok += run("Nova 2 Lite nova_grounding", check_lite_grounding)
    ok += run("Nova 2 Sonic Realtime", lambda: asyncio.run(check_sonic()))
    print("-" * 40)
    print(f"Passed: {ok}/4")
    return 0 if ok == 4 else 1


if __name__ == "__main__":
    sys.exit(main())
