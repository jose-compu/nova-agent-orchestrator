"""Unit tests for UI Listen button flow (status updates, transcript to entry)."""
import queue
import threading
import tkinter as tk
import pytest

try:
    import tkinter
    HAS_TK = True
except ImportError:
    HAS_TK = False


def _run_listen_flow(
    status_var: tk.StringVar,
    user_entry: tk.Entry,
    listen_btn: tk.Misc,
    root: tk.Tk,
    record_and_transcribe_fn,
    send_fn,
):
    """Same logic as ui.do_listen: update status, run worker, poll queue, set entry and status."""
    try:
        status_var.set("Listening... (5 s)")
        root.update_idletasks()
        listen_btn.config(state=tk.DISABLED)
    except Exception:
        pass
    q = queue.Queue()

    def worker():
        try:
            transcript = record_and_transcribe_fn(5.0)
            q.put(("ok", (transcript or "").strip()))
        except Exception as e:
            q.put(("err", str(e)))

    threading.Thread(target=worker, daemon=True).start()

    def check():
        try:
            item = q.get_nowait()
            listen_btn.config(state=tk.NORMAL)
            if item[0] == "err":
                status_var.set(f"Listen error: {item[1]}")
                return
            val = item[1] if len(item) > 1 else ""
            if val:
                user_entry.delete(0, tk.END)
                user_entry.insert(0, val)
                root.after(50, send_fn)
                status_var.set("Ready.")
            else:
                status_var.set("No speech detected. Try again.")
            return
        except queue.Empty:
            pass
        root.after(150, check)

    root.after(200, check)


def _process_until(root, condition, max_iters=100):
    """Run event loop until condition() is True or max_iters."""
    for _ in range(max_iters):
        root.update()
        root.update_idletasks()
        if condition():
            return
    root.update()
    root.update_idletasks()


def _run_mainloop_until(root, condition, timeout_ms=2000):
    """Run mainloop until condition() is True, polling every 50ms."""
    def tick():
        if condition():
            root.quit()
        else:
            root.after(50, tick)
    root.after(50, tick)
    root.after(timeout_ms, root.quit)
    root.mainloop()


@pytest.mark.skipif(not HAS_TK, reason="tkinter not available")
def test_listen_sets_status_immediately():
    """Pressing Listen should set status to 'Listening... (5 s)' right away."""
    root = tk.Tk()
    root.withdraw()
    status_var = tk.StringVar(value="Ready.")
    user_entry = tk.Entry(root)
    listen_btn = tk.Button(root, text="Listen")
    sent = []

    def noop_send():
        sent.append(1)

    def mock_record(_duration):
        return "hello"

    _run_listen_flow(
        status_var, user_entry, listen_btn, root,
        mock_record, noop_send,
    )
    root.update_idletasks()
    assert status_var.get() == "Listening... (5 s)"
    root.destroy()


@pytest.mark.skipif(not HAS_TK, reason="tkinter not available")
def test_listen_after_result_updates_entry_and_status():
    """After mock record_and_transcribe returns, status becomes Ready and entry gets transcript."""
    root = tk.Tk()
    root.withdraw()
    status_var = tk.StringVar(value="Ready.")
    user_entry = tk.Entry(root)
    user_entry.pack()
    listen_btn = tk.Button(root, text="Listen")
    listen_btn.pack()
    sent = []

    def noop_send():
        sent.append(1)

    def mock_record(_duration):
        return "hello world"

    _run_listen_flow(
        status_var, user_entry, listen_btn, root,
        mock_record, noop_send,
    )
    def done():
        return status_var.get() == "Ready." and user_entry.get() == "hello world"
    _run_mainloop_until(root, done)
    assert status_var.get() == "Ready."
    assert user_entry.get() == "hello world"
    root.destroy()


@pytest.mark.skipif(not HAS_TK, reason="tkinter not available")
def test_listen_no_speech_shows_message():
    """When record returns empty string, status shows 'No speech detected'."""
    root = tk.Tk()
    root.withdraw()
    status_var = tk.StringVar(value="Ready.")
    user_entry = tk.Entry(root)
    user_entry.pack()
    listen_btn = tk.Button(root, text="Listen")
    listen_btn.pack()

    def mock_record(_duration):
        return ""

    _run_listen_flow(
        status_var, user_entry, listen_btn, root,
        mock_record, lambda: None,
    )
    def done():
        return "No speech detected" in status_var.get()
    _run_mainloop_until(root, done)
    assert "No speech detected" in status_var.get()
    root.destroy()
