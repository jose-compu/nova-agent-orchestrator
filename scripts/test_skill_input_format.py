#!/usr/bin/env python3
"""
Test skill input formatting: format_task_for_script + docx run.py.
Run from project root: python scripts/test_skill_input_format.py
Uses NOVA_API_KEY for format_task_for_script; if unset, uses raw task (docx fallback to sample data).
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from skills_tools import get_skill_root, load_skill_content
from skill_to_python import (
    format_task_for_script,
    convert_and_run,
    _infer_script_input_format,
)


def test_infer_format():
    """Test that we infer JSON format from docx run.py."""
    run_py = ROOT / "skills" / "skills" / "docx" / "run.py"
    if not run_py.exists():
        print("SKIP: docx/run.py not found")
        return
    text = run_py.read_text(encoding="utf-8", errors="replace")
    spec = _infer_script_input_format(text)
    print("_infer_script_input_format(docx/run.py):", repr(spec)[:120])
    assert "json" in spec.lower() or "JSON" in spec, "Expected JSON hint for docx script"
    print("  -> OK")
    return True


def test_docx_with_raw_task():
    """Run docx skill with natural-language task (no Nova); script should use sample data."""
    print("\n--- test_docx_with_raw_task ---")
    skill_name = "docx"
    content = load_skill_content(skill_name)
    skill_root = get_skill_root(skill_name)
    if not content or not skill_root:
        print("SKIP: docx skill not found")
        return False
    task = "please generate a wine list with some red and white wines"
    ok, output = convert_and_run(
        skill_name,
        content,
        task,
        api_key=None,
        skill_root=skill_root,
    )
    print("ok:", ok)
    print("output:", output[:500] if output else "(none)")
    if ok:
        out_file = skill_root / "wine_list.docx"
        assert out_file.exists(), "wine_list.docx should exist"
        print("  -> wine_list.docx created")
    return ok


def test_docx_with_formatted_task():
    """Run docx skill with Nova-generated JSON task (requires NOVA_API_KEY)."""
    print("\n--- test_docx_with_formatted_task ---")
    api_key = os.environ.get("NOVA_API_KEY")
    if not api_key or not api_key.strip():
        print("SKIP: NOVA_API_KEY not set")
        return None
    skill_name = "docx"
    content = load_skill_content(skill_name)
    skill_root = get_skill_root(skill_name)
    run_py = skill_root / "run.py"
    if not run_py.exists():
        print("SKIP: docx/run.py not found")
        return False
    script_text = run_py.read_text(encoding="utf-8", errors="replace")
    user_message = "generate a wine list with three wines: a Bordeaux, a Chardonnay, and a Rosé"
    formatted = format_task_for_script(
        skill_name, content, run_py, script_text, user_message, api_key=api_key
    )
    print("formatted task (first 300 chars):", repr((formatted or "")[:300]))
    if formatted:
        import json
        try:
            data = json.loads(formatted.strip())
            print("  -> Valid JSON array, len:", len(data) if isinstance(data, list) else "n/a")
        except Exception as e:
            print("  -> Not valid JSON:", e)
    ok, output = convert_and_run(
        skill_name, content, user_message, api_key=api_key, skill_root=skill_root
    )
    print("ok:", ok)
    print("output:", output[:500] if output else "(none)")
    if ok and skill_root:
        print("  -> wine_list.docx exists:", (skill_root / "wine_list.docx").exists())
    return ok


SAMPLE_FRENCH_WINES = [
    {"name": "Château Margaux", "region": "Bordeaux", "variety": "Cabernet Sauvignon", "year": 2018, "price": 349.99},
    {"name": "Château Haut-Brion", "region": "Bordeaux", "variety": "Bordeaux Blend", "year": 2016, "price": 429.00},
    {"name": "Domaine Leflaive Puligny-Montrachet", "region": "Burgundy", "variety": "Chardonnay", "year": 2019, "price": 189.00},
    {"name": "Domaine de la Romanée-Conti", "region": "Burgundy", "variety": "Pinot Noir", "year": 2017, "price": 18999.00},
    {"name": "Château de Beaucastel", "region": "Rhône", "variety": "Châteauneuf-du-Pape", "year": 2016, "price": 89.00},
    {"name": "Domaine Tempier Bandol", "region": "Provence", "variety": "Mourvèdre", "year": 2020, "price": 52.00},
]


def test_xlsx_french_wines_sample():
    """Run xlsx skill with sample French wines JSON (by region and price)."""
    print("\n--- test_xlsx_french_wines_sample ---")
    import json
    skill_name = "xlsx"
    content = load_skill_content(skill_name)
    skill_root = get_skill_root(skill_name)
    if not content or not skill_root:
        print("SKIP: xlsx skill not found")
        return False
    task = json.dumps(SAMPLE_FRENCH_WINES)
    ok, output = convert_and_run(skill_name, content, task, api_key=None, skill_root=skill_root)
    print("ok:", ok, "output:", (output or "")[:350])
    if ok:
        out_file = skill_root / "french_wines.xlsx"
        if not out_file.exists():
            out_file = ROOT / "french_wines.xlsx"
        assert out_file.exists(), "french_wines.xlsx should exist"
        print("  -> french_wines.xlsx created")
    return ok


def test_xlsx_french_wines_formatted():
    """Run xlsx skill with user message; Nova generates JSON (requires NOVA_API_KEY)."""
    print("\n--- test_xlsx_french_wines_formatted ---")
    api_key = os.environ.get("NOVA_API_KEY")
    if not api_key or not api_key.strip():
        print("SKIP: NOVA_API_KEY not set")
        return None
    skill_name = "xlsx"
    content = load_skill_content(skill_name)
    skill_root = get_skill_root(skill_name)
    run_py = skill_root / "run.py"
    if not run_py.exists():
        print("SKIP: xlsx/run.py not found")
        return False
    script_text = run_py.read_text(encoding="utf-8", errors="replace")
    user_message = "generate a spreadsheet with French wines by region and prices"
    formatted = format_task_for_script(
        skill_name, content, run_py, script_text, user_message, api_key=api_key
    )
    print("formatted (first 250):", repr((formatted or "")[:250]))
    if formatted:
        import json as _json
        try:
            data = _json.loads(formatted.strip())
            print("  -> JSON array len:", len(data) if isinstance(data, list) else "n/a")
        except Exception as e:
            print("  -> Not JSON:", e)
    ok, output = convert_and_run(
        skill_name, content, user_message, api_key=api_key, skill_root=skill_root
    )
    print("ok:", ok, "output:", (output or "")[:350])
    if ok and skill_root:
        print("  -> french_wines.xlsx exists:", (skill_root / "french_wines.xlsx").exists() or (ROOT / "french_wines.xlsx").exists())
    return ok


if __name__ == "__main__":
    print("Testing skill input formatting")
    test_infer_format()
    ok1 = test_docx_with_raw_task()
    ok2 = test_docx_with_formatted_task()
    ok3 = test_xlsx_french_wines_sample()
    ok4 = test_xlsx_french_wines_formatted()
    print("\n--- Result ---")
    print("docx raw task:", "OK" if ok1 else "FAIL")
    print("docx formatted task:", "OK" if ok2 is True else ("SKIP" if ok2 is None else "FAIL"))
    print("xlsx French wines (sample):", "OK" if ok3 else "FAIL")
    print("xlsx French wines (formatted):", "OK" if ok4 is True else ("SKIP" if ok4 is None else "FAIL"))
    sys.exit(0 if (ok1 and (ok2 is True or ok2 is None) and ok3 and (ok4 is True or ok4 is None)) else 1)
