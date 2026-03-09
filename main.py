"""Entrypoint: run the Nova Speech-to-Speech Assistant (UI or CLI)."""
import argparse
import asyncio
import sys

try:
    from ui import run_ui
except ImportError:
    run_ui = None  # e.g. tkinter not available


def run_cli(speak: bool = False) -> None:
    """Text-only loop: type message, get answer. Displays same concise text as speech (no full search dump)."""
    import os
    from modes import add_short_term_from_response
    from orchestrator import run_turn_stream
    from speech_utils import text_for_speech
    try:
        from mic_input import record_and_transcribe
        HAS_MIC = True
    except ImportError:
        HAS_MIC = False
        def record_and_transcribe(*args, **kwargs):
            return ""

    print("Nova Assistant (CLI). Type a message and press Enter. Empty line or Ctrl+C to quit.")
    if HAS_MIC:
        print("Type 'mic' or 'listen' to speak your message instead.")
        try:
            from mic_input import check_mic_and_print_tips
            check_mic_and_print_tips()
        except Exception:
            pass
        print()
    else:
        print()
    while True:
        try:
            msg = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if not msg:
            continue
        if HAS_MIC and msg.lower() in ("mic", "listen"):
            print("[Listening... speak now (5 s)]", flush=True)
            msg = record_and_transcribe(5.0)
            if not msg:
                print("No speech detected. Check: System Settings > Privacy & Security > Microphone (allow Terminal), and speak clearly during the 5 s.", flush=True)
                continue
            print(f"You said: {msg}")
        print("Assistant: ", end="", flush=True)
        full = []
        for chunk in run_turn_stream(msg, on_status=lambda s: print(f"\r[{s}] ", end="", flush=True)):
            full.append(chunk)
        text = "".join(full)
        add_short_term_from_response(text)
        to_show = text_for_speech(text, max_chars=180, use_ai_summary=True, api_key=os.environ.get("NOVA_API_KEY"))
        if not to_show:
            to_show = text[:180] if text else ""
        print(f"\rAssistant: {to_show}")
        if speak and to_show:
            try:
                from nova_client import realtime_tts
                import numpy as np
                import sounddevice as sd
                print("[Speaking...]", flush=True)
                audio, transcript = asyncio.run(realtime_tts(to_show, instructions="Say this concisely."))
                if audio:
                    arr = np.frombuffer(audio, dtype=np.int16)
                    arr = np.ascontiguousarray(arr.astype(np.float32) / 32768.0)
                    sd.play(arr, 24000, blocking=True)
                if transcript:
                    print(f"Spoken: {transcript}", flush=True)
                print("[Done]", flush=True)
            except Exception as e:
                print(f"[Speak error: {e}]", flush=True)
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Nova Speech-to-Speech Assistant")
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode (no GUI)")
    parser.add_argument("--speak", action="store_true", help="Use Nova Sonic TTS after each reply (CLI only)")
    args = parser.parse_args()

    if args.cli:
        run_cli(speak=args.speak)
        return

    if run_ui is None:
        print("Tkinter not available. Run with --cli for text mode: python main.py --cli", file=sys.stderr)
        sys.exit(1)
    run_ui()


if __name__ == "__main__":
    main()
