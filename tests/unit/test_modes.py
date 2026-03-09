"""Unit tests for modes (Memory Mode triggers and memory context)."""
import json
import os
import tempfile

import pytest

# Test with a temp memory file to avoid polluting user data
@pytest.fixture
def temp_memory_file(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        with open(path, "w") as f:
            json.dump([], f)
        import modes as mod
        monkeypatch.setattr(mod, "_MEMORY_FILE", path)
        yield path
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


def test_memory_mode_trigger(temp_memory_file):
    import modes
    modes.set_mode("normal")
    r = modes.handle_mode_command("Memory Mode")
    assert r == "Entering Memory Mode"
    assert modes.get_mode() == "memory"


def test_exit_memory_mode_trigger(temp_memory_file):
    import modes
    modes.set_mode("memory")
    r = modes.handle_mode_command("Exit Memory Mode")
    assert r == "Exiting Memory Mode"
    assert modes.get_mode() == "normal"


def test_memory_mode_stores_fact(temp_memory_file):
    import modes
    modes.set_mode("memory")
    modes.handle_mode_command("Memory Mode")
    r = modes.handle_mode_command("I only use Celsius for weather")
    assert "Storing New Memory" in r and "I only use Celsius for weather" in r
    mem = modes.get_memory()
    assert any(i.get("fact") == "I only use Celsius for weather" for i in mem)
    modes.set_mode("normal")


def test_normal_message_not_handled(temp_memory_file):
    import modes
    modes.set_mode("normal")
    assert modes.handle_mode_command("What is the weather?") is None


def test_erase_memories_clears(temp_memory_file):
    import modes
    modes.add_memory("Some fact", about="user")
    assert len(modes.get_memory()) == 1
    r = modes.handle_mode_command("erase memories")
    assert r == "Memories cleared."
    assert len(modes.get_memory()) == 0
