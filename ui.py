"""
Chat-style Tkinter UI: conversation history, streamed replies, local TTS (macOS say / pyttsx3).
"""
import asyncio
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk

try:
    import sounddevice as sd
    import numpy as np
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False

from orchestrator import run_turn_stream
from modes import add_short_term_from_response
from speech_utils import text_for_speech
try:
    from mic_input import record_and_transcribe, record_until_stop, transcribe_audio, HAS_DEPS as HAS_MIC
except ImportError:
    HAS_MIC = False
    def record_and_transcribe(*args, **kwargs):
        return ""
    def record_until_stop(*args, **kwargs):
        return None, None
    def transcribe_audio(*args, **kwargs):
        return ""


SAMPLE_RATE = 24000

# When present in streamed reply, only the text before this marker is spoken; full text is shown in chat.
DISPLAY_ONLY_MARKER = "\0DISPLAY_ONLY"


def _sanitize_for_speech(text: str) -> str:
    """Remove URLs, parentheticals like (domain.com), leading hyphens; normalize spaces."""
    if not text or not isinstance(text, str):
        return ""
    s = text.strip()
    s = re.sub(r"\s*\([^)]*\.(?:com|org|net|fr)[^)]*\)", "", s, flags=re.I)  # (domain.com)
    s = re.sub(r"\s*\([^)]*\)", "", s)  # any remaining (...)
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"^\s*[-–—]\s*", "", s)  # leading dash/bullet
    s = re.sub(r"\s*[-–—]\s*", ", ", s)  # bullet points -> comma
    s = " ".join(s.split()).replace('"', "'").strip()
    return s  # no length cap; say it all


def _speak_local(text: str, process_holder: list | None = None) -> None:
    """Speak text using macOS 'say' or pyttsx3. If process_holder is a list, the current say process is stored so it can be terminated."""
    raw = _sanitize_for_speech(text)
    if not raw:
        return
    if sys.platform == "darwin":
        try:
            if process_holder is not None:
                proc = subprocess.Popen(
                    ["say", "-f", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                process_holder.clear()
                process_holder.append(proc)
                try:
                    proc.stdin.write(raw.encode("utf-8"))
                    proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass
                proc.wait()
            else:
                subprocess.run(
                    ["say", "-f", "-"],
                    input=raw.encode("utf-8"),
                    check=True,
                    timeout=120,
                    capture_output=True,
                )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            pass
        finally:
            if process_holder is not None and process_holder:
                process_holder.clear()
    else:
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.say(raw)
            engine.runAndWait()
        except Exception:
            pass


def _play_pcm(pcm_bytes: bytes) -> None:
    if not HAS_SOUNDDEVICE or not pcm_bytes:
        return
    arr = np.frombuffer(pcm_bytes, dtype=np.int16)
    arr = np.ascontiguousarray(arr.astype(np.float32) / 32768.0)
    sd.play(arr, SAMPLE_RATE, blocking=True)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def run_ui():
    root = tk.Tk()
    root.title("Nova Agent Orchestrator")
    root.geometry("880x640")
    root.minsize(620, 420)

    # Refined palette and fonts
    FONT_MAIN = ("Helvetica Neue", 12)
    FONT_STATUS = ("Helvetica Neue", 11)
    BG_ROOT = "#eaeef2"
    BG_CONV = "#fafbfc"
    TEXT_PRIMARY = "#1f2328"
    TEXT_SECONDARY = "#57606a"
    USER_ACCENT = "#0550ae"
    ASSISTANT_ACCENT = "#1a7f37"
    ENTRY_BG = "#ffffff"
    ENTRY_BORDER = "#54aeff"
    ENTRY_FG = "#1f2328"
    CURSOR_COLOR = "#0550ae"

    root.configure(background=BG_ROOT)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG_ROOT, foreground=TEXT_PRIMARY, font=FONT_MAIN)
    style.configure("TFrame", background=BG_ROOT)
    style.configure("TLabel", background=BG_ROOT, foreground=TEXT_SECONDARY, font=FONT_STATUS)
    style.configure("TButton", background="#ddf4ff", foreground=USER_ACCENT, font=FONT_MAIN)
    style.map("TButton", background=[("active", "#b6e3ff")], foreground=[("active", "#0550ae")])

    # Logo header (nova.png next to ui.py or in cwd)
    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nova.png")
    header_frame = ttk.Frame(root, padding=(8, 6))
    header_frame.pack(fill=tk.X)
    logo_photo = None
    if os.path.isfile(logo_path):
        try:
            logo_photo = tk.PhotoImage(file=logo_path)
            w, h = logo_photo.width(), logo_photo.height()
            if max(w, h) > 120:
                factor = max(1, w // 120, h // 36)
                logo_photo = logo_photo.subsample(factor, factor)
            logo_label = ttk.Label(header_frame, image=logo_photo)
            logo_label.image = logo_photo
            logo_label.pack(side=tk.LEFT)
        except tk.TclError:
            pass

    # Conversation history (chat-style)
    conv_frame = ttk.Frame(root, padding=(5, 5))
    conv_frame.pack(fill=tk.BOTH, expand=True)
    conversation = scrolledtext.ScrolledText(
        conv_frame,
        wrap=tk.WORD,
        state=tk.NORMAL,
        font=FONT_MAIN,
        padx=10,
        pady=8,
        foreground=TEXT_PRIMARY,
        background=BG_CONV,
        insertbackground=CURSOR_COLOR,
    )
    conversation.pack(fill=tk.BOTH, expand=True)
    conversation.tag_configure("user_label", font=(FONT_MAIN[0], FONT_MAIN[1], "bold"), foreground=USER_ACCENT)
    conversation.tag_configure("user_msg", foreground=TEXT_PRIMARY)
    conversation.tag_configure("assistant_label", font=(FONT_MAIN[0], FONT_MAIN[1], "bold"), foreground=ASSISTANT_ACCENT)
    conversation.tag_configure("assistant_msg", foreground=TEXT_PRIMARY)
    conversation.tag_configure("status", foreground=TEXT_SECONDARY, font=FONT_STATUS)

    last_assistant_text: list[str] = []  # [0] = text to speak (last reply)

    # Current TTS process so we can stop it when user presses Listen
    speaking_proc: list = []

    # Status line
    status_var = tk.StringVar(value="Ready.")
    status_label = ttk.Label(root, textvariable=status_var, foreground=TEXT_SECONDARY)
    status_label.pack(anchor=tk.W, padx=8, pady=(0, 2))

    # Input row
    input_frame = ttk.Frame(root, padding=(5, 5))
    input_frame.pack(fill=tk.X)
    user_entry = tk.Entry(
        input_frame,
        width=50,
        font=FONT_MAIN,
        bg=ENTRY_BG,
        fg=ENTRY_FG,
        insertbackground=CURSOR_COLOR,
        highlightthickness=2,
        highlightbackground=ENTRY_BORDER,
        highlightcolor=ENTRY_BORDER,
        relief=tk.FLAT,
    )
    user_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
    user_entry.focus_set()

    def append_user(msg: str):
        conversation.insert(tk.END, "You: ", "user_label")
        conversation.insert(tk.END, msg.strip() + "\n\n", "user_msg")
        conversation.see(tk.END)

    def append_assistant_start():
        conversation.insert(tk.END, "Nova: ", "assistant_label")
        conversation.mark_set("nova_start", tk.END)
        conversation.mark_gravity("nova_start", tk.LEFT)
        conversation.see(tk.END)

    def replace_assistant_stream_with(final_text: str):
        try:
            start = conversation.index("nova_start")
            end = conversation.index(tk.END)
            conversation.delete(start, end)
            conversation.insert(start, final_text + "\n\n", "assistant_msg")
            conversation.see(tk.END)
        except tk.TclError:
            conversation.insert(tk.END, final_text + "\n\n", "assistant_msg")
            conversation.see(tk.END)

    def speak_response():
        text = (last_assistant_text[0] if last_assistant_text else "").strip()
        if not text:
            status_var.set("No reply to speak.")
            return
        status_var.set("Speaking...")
        root.update_idletasks()
        q_main = queue.Queue()
        def do_tts():
            try:
                import os
                to_speak = text_for_speech(text, max_chars=180, use_ai_summary=True, api_key=os.environ.get("NOVA_API_KEY"))
                if not to_speak:
                    to_speak = text[:180]
                _speak_local(to_speak, process_holder=speaking_proc)
            except Exception as e:
                q_main.put(("status", f"Speak error: {e}"))
            q_main.put(("status", "Ready."))
        threading.Thread(target=do_tts, daemon=True).start()
        def check():
            try:
                while True:
                    cmd, val = q_main.get_nowait()
                    if cmd == "status":
                        status_var.set(val)
            except queue.Empty:
                pass
            root.after(200, check)
        root.after(200, check)

    def send():
        msg = user_entry.get().strip()
        if not msg:
            return
        user_entry.delete(0, tk.END)
        append_user(msg)
        status_var.set("Thinking...")
        speak_btn.config(state=tk.DISABLED)
        listen_btn.config(state=tk.DISABLED)
        root.update_idletasks()
        result_queue = queue.Queue()
        append_assistant_start()

        def worker():
            try:
                full = []
                for chunk in run_turn_stream(msg, on_status=lambda s: result_queue.put(("status", s))):
                    full.append(chunk)
                    result_queue.put(("chunk", chunk))
                result_queue.put(("done", "".join(full)))
            except Exception as e:
                result_queue.put(("error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            try:
                while True:
                    cmd, val = result_queue.get_nowait()
                    if cmd == "status":
                        status_var.set(val)
                    elif cmd == "chunk":
                        pass
                    elif cmd == "done":
                        val = val or ""
                        add_short_term_from_response(val)
                        print("[ui] streamed_reply (full):", repr(val[:200]), flush=True)
                        replace_assistant_stream_with("...")
                        status_var.set("Speaking...")
                        root.update_idletasks()
                        # If reply contains DISPLAY_ONLY_MARKER, show full text in chat but only speak the part before it.
                        if DISPLAY_ONLY_MARKER in val:
                            to_speak = val.split(DISPLAY_ONLY_MARKER)[0].strip()
                            before, _, after = val.partition(DISPLAY_ONLY_MARKER)
                            to_display = (before.strip() + "\n\n" + after.strip()).strip() if after.strip() else before.strip()
                        else:
                            to_speak = val
                            to_display = val
                        result_queue.put(("sonic_transcript", to_display))
                        def do_tts():
                            try:
                                _speak_local(to_speak, process_holder=speaking_proc)
                            except Exception as e:
                                result_queue.put(("status", f"Speak error: {e}"))
                        threading.Thread(target=do_tts, daemon=True).start()
                    elif cmd == "sonic_transcript":
                        transcript = (val if isinstance(val, str) else "").strip()
                        replace_assistant_stream_with(transcript)
                        last_assistant_text.clear()
                        last_assistant_text.append(transcript)
                        status_var.set("Ready.")
                        speak_btn.config(state=tk.NORMAL)
                        listen_btn.config(state=tk.NORMAL)
                        return
                    elif cmd == "error":
                        replace_assistant_stream_with(f"Error: {val}")
                        status_var.set(f"Error: {val}")
                        speak_btn.config(state=tk.NORMAL)
                        listen_btn.config(state=tk.NORMAL)
                        return
            except queue.Empty:
                pass
            root.after(100, poll)
        root.after(100, poll)

    send_btn = ttk.Button(input_frame, text="Send", command=send)
    send_btn.pack(side=tk.LEFT, padx=(0, 4))
    speak_btn = ttk.Button(input_frame, text="Speak", command=speak_response)
    speak_btn.pack(side=tk.LEFT, padx=(0, 4))

    listen_stop_event: list = []  # [Event] when set, recording stops

    def do_listen():
        # Stop any ongoing TTS when user presses Listen
        if speaking_proc:
            try:
                p = speaking_proc[0]
                if p is not None and p.poll() is None:
                    p.terminate()
            except Exception:
                pass
            speaking_proc.clear()
        if listen_stop_event and listen_stop_event[0] is not None:
            # User pressed Stop: signal worker to finish
            listen_stop_event[0].set()
            return
        status_var.set("Listening... Press Stop when done.")
        root.update_idletasks()
        listen_btn.config(text="Stop")
        stop_ev = threading.Event()
        listen_stop_event.clear()
        listen_stop_event.append(stop_ev)
        q = queue.Queue()

        def worker():
            try:
                sr, samples = record_until_stop(stop_ev, chunk_sec=0.15, max_duration_sec=120.0)
                if sr is not None and samples is not None and samples.size > 0:
                    duration_sec = samples.size / sr
                    t = transcribe_audio(samples, sr)
                    q.put(("ok", t, duration_sec))
                else:
                    q.put(("ok", "", 0.0))
            except Exception as e:
                q.put(("err", str(e)))
            listen_stop_event.clear()
            listen_stop_event.append(None)

        threading.Thread(target=worker, daemon=True).start()

        def check():
            try:
                item = q.get_nowait()
                listen_btn.config(text="Listen")
                if item[0] == "err":
                    status_var.set(f"Listen error: {item[1]}")
                    return
                # item is ("ok", transcript, duration_sec)
                val = item[1] if len(item) > 1 else ""
                duration_sec = item[2] if len(item) > 2 else 0.0
                if val:
                    user_entry.delete(0, tk.END)
                    user_entry.insert(0, val)
                    root.after(50, send)
                    status_var.set("Ready.")
                elif duration_sec < 0.5:
                    status_var.set("Recording too short. Speak at least 1 second, then press Stop.")
                else:
                    status_var.set("Speech unclear or not recognized. Try again.")
                return
            except queue.Empty:
                pass
            root.after(150, check)
        root.after(200, check)

    listen_btn = ttk.Button(input_frame, text="Listen", command=do_listen)
    listen_btn.pack(side=tk.LEFT)
    if not HAS_MIC:
        listen_btn.config(state=tk.DISABLED)

    def _send_on_return(event):
        if event.keysym == "Return" and not event.state & 0x1:
            send()
            return "break"
        return None
    user_entry.bind("<Return>", _send_on_return)

    root.mainloop()


if __name__ == "__main__":
    run_ui()
