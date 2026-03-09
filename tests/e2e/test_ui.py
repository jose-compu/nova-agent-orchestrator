"""E2E: UI module loads and run_ui is callable (no headless run to avoid blocking)."""
import pytest

try:
    import tkinter
    HAS_TK = True
except ImportError:
    HAS_TK = False


@pytest.mark.skipif(not HAS_TK, reason="tkinter not available")
def test_ui_module_imports():
    from ui import run_ui
    assert callable(run_ui)


@pytest.mark.skipif(not HAS_TK, reason="tkinter not available")
def test_main_module_imports():
    import main
    assert hasattr(main, "run_ui")
