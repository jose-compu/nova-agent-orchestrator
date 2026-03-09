"""
Convert a Claude Code skill (SKILL.md content) on-the-fly into a minimalistic Python script,
persist it under skills-python/, and run it with the user's task context.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from file_tools import add_recent_paths
from nova_client import chat

SKILLS_PYTHON_DIR = Path(__file__).resolve().parent / "skills-python"
PROJECT_ROOT = Path(__file__).resolve().parent
MAX_SKILL_CONTENT_FOR_GEN = 8000
MAX_SCRIPT_LINES = 200
# Prefix so generated scripts never shadow Python packages (e.g. docx -> skill_docx.py)
SKILL_SCRIPT_PREFIX = "skill_"

# Map import names to pip package names (e.g. docx -> python-docx)
IMPORT_TO_PIP: dict[str, str] = {
    "docx": "python-docx",
    "pypdf": "pypdf",
    "pdfplumber": "pdfplumber",
    "reportlab": "reportlab",
    "openpyxl": "openpyxl",
    "xlsxwriter": "xlsxwriter",
    "pandas": "pandas",
}


def _get_venv_python() -> Path | None:
    """Return path to project .venv Python, or None if not found."""
    venv = PROJECT_ROOT / ".venv"
    if sys.platform == "win32":
        exe = venv / "Scripts" / "python.exe"
    else:
        exe = venv / "bin" / "python"
    return exe if exe.exists() else None


def _parse_pip_deps_from_script(script_text: str) -> list[str]:
    """Extract pip package names from script comment (e.g. '# Required pip packages: python-docx') or from import names."""
    deps: list[str] = []
    skip_words = {"pip", "install", "packages", "required", "package"}
    lines = (script_text or "").splitlines()
    # First pass: comment in first 25 lines
    for line in lines[:25]:
        s = line.strip()
        if s.startswith("#") and ("pip" in s.lower() or "required" in s.lower()):
            rest = re.sub(r"^#\s*", "", s).split(":", 1)[-1].strip()
            for part in re.split(r"[\s,]+", rest):
                part = part.strip()
                if part and part not in skip_words and len(part) > 2:
                    deps.append(part)
            if deps:
                return deps
    # Second pass: known imports anywhere in script
    for line in lines:
        for imp in ("docx", "pypdf", "pdfplumber", "reportlab", "openpyxl", "xlsxwriter", "pandas"):
            if f"from {imp} " in line or f"import {imp}" in line:
                pip_name = IMPORT_TO_PIP.get(imp)
                if pip_name and pip_name not in deps:
                    deps.append(pip_name)
    return deps


def _ensure_deps_installed(deps: list[str], python_exe: Path, timeout_sec: int = 120) -> bool:
    """Run pip install for deps using the given Python. Returns True if success."""
    if not deps:
        return True
    try:
        subprocess.run(
            [str(python_exe), "-m", "pip", "install", "-q"] + deps,
            capture_output=True,
            timeout=timeout_sec,
            cwd=PROJECT_ROOT,
        )
        return True
    except (subprocess.TimeoutExpired, Exception):
        return False


def _slug(name: str) -> str:
    """Turn skill name into a safe filename (no spaces, single extension)."""
    s = (name or "").strip().lower()
    s = re.sub(r"[^\w\-]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "skill"


def is_skill_suitable_for_python(
    skill_name: str,
    skill_content: str,
    task: str,
    api_key: str | None = None,
) -> tuple[bool, str]:
    """
    Ask Nova Lite if this skill can be implemented as a single runnable Python script.
    Returns (True, "") if suitable, (False, reason) if not (e.g. interactive-only, requires GUI, etc.).
    """
    snippet = (skill_content or "").strip()[:2500]
    task = (task or "").strip()[:300]
    prompt = f"""You are deciding whether a Claude Code skill can be turned into a single, runnable Python script that takes the user's task as input (e.g. sys.argv[1]) and prints a result to stdout.

SKILL NAME: {skill_name}

SKILL CONTENT (excerpt):
{snippet}

USER TASK: {task}

Answer with exactly one line:
- "SUITABLE" if the skill can be implemented as one non-interactive Python script that reads the task and produces output (e.g. file operations, text/code generation, data processing). 
- "NOT_SUITABLE: <short reason>" if the skill is not suitable—e.g. it requires interactive steps (asking the user and waiting), showing files for visual choice, multi-step human-in-the-loop, or cannot be expressed as a single script. Keep the reason brief.

Only one line, no other text."""
    try:
        resp = chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=80,
            api_key=api_key,
        )
        if not resp.choices or not resp.choices[0].message.content:
            return True, ""  # assume suitable if check fails
        line = (resp.choices[0].message.content or "").strip().upper()
        if line.startswith("NOT_SUITABLE"):
            reason = resp.choices[0].message.content.strip()
            if ":" in reason:
                reason = reason.split(":", 1)[1].strip()
            else:
                reason = "this skill is not suitable for a single runnable Python script."
            return False, reason
        return True, ""
    except Exception:
        return True, ""


def generate_skill_script(
    skill_name: str,
    skill_content: str,
    task: str,
    api_key: str | None = None,
) -> str | None:
    """
    Use Nova Lite to generate a minimal, self-contained Python script from the skill
    that accomplishes the user task. Only main features; no optional or advanced parts.
    Returns script text or None on failure.
    """
    content = (skill_content or "")[:MAX_SKILL_CONTENT_FOR_GEN]
    task = (task or "").strip()[:500]
    prompt = f"""You are converting a Claude Code skill into a single, minimalistic Python script.

SKILL NAME: {skill_name}

--- SKILL CONTENT (use only the main features described) ---
{content}
--- END SKILL ---

USER TASK: {task}

Requirements:
- Output ONLY valid Python code. No markdown, no explanation before or after.
- The script must accept the task description via sys.argv[1] (if provided) or sys.stdin.
- Implement only the main features needed to fulfill the task. Omit optional/advanced sections.
- Keep it under {MAX_SCRIPT_LINES} lines. Prefer standard library; if the skill requires third-party libs (e.g. pypdf, reportlab), use them and add a short comment at the top listing required pip packages.
- Print the result or outcome to stdout so the user sees it.
- If the task cannot be done by running a script (e.g. interactive steps only), print a clear message explaining what the user should do instead.
"""
    try:
        resp = chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8192,
            api_key=api_key,
        )
        if not resp.choices or not resp.choices[0].message.content:
            return None
        raw = (resp.choices[0].message.content or "").strip()
        # Strip markdown code fence if present
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines)
        return raw if raw else None
    except Exception:
        return None


def _infer_script_input_format(script_text: str, max_lines: int = 120) -> str:
    """Infer expected input format from script comments and error messages."""
    if not script_text:
        return "structured data appropriate for the skill (e.g. JSON)"
    lines = script_text.splitlines()[:max_lines]
    spec_parts = []
    for line in lines:
        s = line.strip()
        if "expected input" in s.lower() or "input format" in s.lower():
            rest = s.split(":", 1)[-1].strip() if ":" in s else s
            if len(rest) > 10:
                spec_parts.append(rest)
        if "json array" in s.lower() and ("field" in s.lower() or "object" in s.lower()):
            spec_parts.append(s)
        if "fields:" in s.lower() or "with fields" in s.lower():
            spec_parts.append(s)
    if spec_parts:
        return " ".join(spec_parts)[:500]
    full = script_text[:3000]
    if "json.loads" in full or "import json" in full:
        return "JSON (e.g. array of objects). If the user asks for a list, table, or database of items, output a JSON array with the appropriate fields for each item."
    return "structured data appropriate for the skill (e.g. JSON). Generate concrete data from the user request."


def format_task_for_script(
    skill_name: str,
    skill_content: str,
    script_path: Path | None,
    script_text: str | None,
    user_message: str,
    api_key: str | None = None,
) -> str | None:
    """Use Nova to generate the exact input the skill script expects (e.g. JSON). Returns formatted task or None."""
    if not (user_message or "").strip():
        return None
    format_spec = "structured data (e.g. JSON) appropriate for the skill"
    if script_text:
        format_spec = _infer_script_input_format(script_text)
    skill_excerpt = (skill_content or "")[:1500].strip()
    prompt = f"""You are generating the exact input to pass to a Python skill script.

Skill name: {skill_name}
Skill context (excerpt):
{skill_excerpt}

Expected script input format: {format_spec}

User request: {user_message.strip()[:600]}

Generate ONLY the data to pass to the script. No explanation, no markdown, no code fence. If the script expects JSON, output only valid JSON. If it expects a file path, output the path. Output nothing else."""
    try:
        resp = chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            api_key=api_key,
        )
        if not resp.choices or not resp.choices[0].message.content:
            return None
        raw = (resp.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines)
        return raw if raw else None
    except Exception:
        return None


def _extract_skill_output_paths(output: str, cwd: Path) -> list[str]:
    """
    Parse skill script stdout for output file paths. Supports:
    - OUTPUT_FILE: path / OUTPUT_PATH: path (relative to cwd if not absolute)
    - "Created <filename>" or "created <filename>"
    - "saved to <path>" / "written to <path>"
    - Absolute paths that exist and have a common file extension.
    Returns list of resolved absolute paths.
    """
    if not output or not cwd.is_dir():
        return []
    seen: set[str] = set()
    result: list[str] = []

    def add(p: str) -> None:
        if not p or not p.strip():
            return
        try:
            path = (cwd / p.strip()).resolve() if not Path(p.strip()).is_absolute() else Path(p.strip()).resolve()
            if path.is_file() and path not in seen:
                seen.add(str(path))
                result.append(str(path))
        except Exception:
            pass

    # OUTPUT_FILE: or OUTPUT_PATH: (allow relative)
    for match in re.finditer(r"(?:OUTPUT_FILE|OUTPUT_PATH)\s*:\s*([^\s\n]+)", output, re.I):
        add(match.group(1).strip())

    # "Created filename.ext" or "created filename.ext"
    for match in re.finditer(r"\b(?:Created|created)\s+([^\s\)]+\.(?:html?|pdf|xlsx?|docx?|png|jpe?g|gif|json|csv|txt))\b", output, re.I):
        add(match.group(1))

    # "saved to path" / "written to path"
    for match in re.finditer(r"\b(?:saved|written)\s+to\s+([^\s\n\.]+(?:\.[a-zA-Z0-9]+)?)", output, re.I):
        add(match.group(1))

    # Absolute paths that look like project outputs (have extension, exist)
    for match in re.finditer(r"(/[^\s\n\[\]<>\"']+\.(?:html?|pdf|xlsx?|docx?|png|jpe?g|gif|json|csv|txt))\b", output):
        p = match.group(1)
        try:
            path = Path(p).resolve()
            if path.is_file() and str(path) not in seen and ".venv" not in p and "site-packages" not in p:
                seen.add(str(path))
                result.append(str(path))
        except Exception:
            pass

    return result


def run_skill_script(
    script_path: Path,
    task: str,
    timeout_sec: int = 60,
    python_exe: Path | None = None,
) -> tuple[bool, str]:
    """
    Run the Python script with task as sys.argv[1]. Uses project .venv Python if available.
    Returns (success, stdout+stderr).
    """
    exe = python_exe or _get_venv_python() or Path(sys.executable)
    try:
        proc = subprocess.run(
            [str(exe), str(script_path), task],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=script_path.parent,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        combined = out + ("\n" + err if err else "")
        return proc.returncode == 0, combined or ("(no output)" if proc.returncode != 0 else "Done.")
    except subprocess.TimeoutExpired:
        return False, "Script timed out."
    except Exception as e:
        return False, str(e)


def convert_and_run(
    skill_name: str,
    skill_content: str,
    task: str,
    api_key: str | None = None,
    skill_root: Path | None = None,
) -> tuple[bool, str]:
    """
    Run the skill as Python: prefer existing run.py/main.py in skill_root when present;
    else reuse skills-python/skill_<slug>.py or skill_root/run.py if exists; else generate,
    save to skill_root/run.py (when skill_root) or skills-python/skill_<slug>.py, and run.
    """
    from skills_tools import find_skill_entrypoint

    # Use project venv Python when available so install and run use the same interpreter
    venv_python = _get_venv_python()
    python_exe = venv_python or Path(sys.executable)

    def install_skill_deps(script_path: Path, root: Path | None) -> None:
        """Install deps from script and skill root requirements.txt before running."""
        script_text = script_path.read_text(encoding="utf-8", errors="replace")
        deps = _parse_pip_deps_from_script(script_text)
        if root and root.is_dir():
            req_file = root / "requirements.txt"
            if req_file.is_file():
                try:
                    for line in req_file.read_text(encoding="utf-8", errors="replace").splitlines():
                        line = line.strip().split("#")[0].strip()
                        if line and not line.startswith("-") and line not in deps:
                            deps.append(line)
                except Exception:
                    pass
        if deps:
            _ensure_deps_installed(deps, python_exe)

    # 1) Prefer existing entrypoint in the skill folder (run.py or main.py)
    if skill_root and skill_root.is_dir():
        entry = find_skill_entrypoint(skill_root)
        if entry:
            install_skill_deps(entry, skill_root)
            script_text = entry.read_text(encoding="utf-8", errors="replace")
            task_to_use = format_task_for_script(
                skill_name, skill_content, entry, script_text, task, api_key
            ) or task
            ok, output = run_skill_script(
                entry, task_to_use, python_exe=python_exe, timeout_sec=120
            )
            if ok:
                add_recent_paths(_extract_skill_output_paths(output, entry.parent))
                return True, output
            # If run failed, fall through to generate or use skills-python

    # 2) When skill has a folder, prefer skill_root/run.py (reuse or create)
    if skill_root and skill_root.is_dir():
        run_py = skill_root / "run.py"
        if run_py.exists():
            install_skill_deps(run_py, skill_root)
            script_text = run_py.read_text(encoding="utf-8", errors="replace")
            task_to_use = format_task_for_script(
                skill_name, skill_content, run_py, script_text, task, api_key
            ) or task
            ok, output = run_skill_script(
                run_py, task_to_use, python_exe=python_exe, timeout_sec=120
            )
            if ok:
                add_recent_paths(_extract_skill_output_paths(output, run_py.parent))
            return ok, output

    # 3) Fallback: skills-python/skill_<slug>.py (reuse or generate)
    SKILLS_PYTHON_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(skill_name)
    script_path = SKILLS_PYTHON_DIR / f"{SKILL_SCRIPT_PREFIX}{slug}.py"

    if script_path.exists():
        script_text = script_path.read_text(encoding="utf-8", errors="replace")
        deps = _parse_pip_deps_from_script(script_text)
        if deps:
            _ensure_deps_installed(deps, python_exe)
        task_to_use = format_task_for_script(
            skill_name, skill_content, script_path, script_text, task, api_key
        ) or task
        ok, output = run_skill_script(
            script_path, task_to_use, python_exe=python_exe
        )
        if ok:
            add_recent_paths(_extract_skill_output_paths(output, script_path.parent))
        return ok, output

    suitable, reason = is_skill_suitable_for_python(
        skill_name, skill_content, task, api_key=api_key
    )
    if not suitable:
        return False, "This skill is not suitable for a Python script. " + (reason or "It cannot be run as a single non-interactive script.")

    script_text = generate_skill_script(skill_name, skill_content, task, api_key=api_key)
    if not script_text:
        return False, "Could not generate a Python script from this skill."

    # Save to skill_root/run.py when available so it becomes the skill's implementation
    if skill_root and skill_root.is_dir():
        script_path = skill_root / "run.py"
    try:
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script_text, encoding="utf-8")
    except Exception as e:
        return False, f"Could not save script: {e}"
    deps = _parse_pip_deps_from_script(script_text)
    if deps:
        _ensure_deps_installed(deps, python_exe)
    task_to_use = format_task_for_script(
        skill_name, skill_content, script_path, script_text, task, api_key
    ) or task
    ok, output = run_skill_script(
        script_path, task_to_use, python_exe=python_exe, timeout_sec=120
    )
    if ok:
        add_recent_paths(_extract_skill_output_paths(output, script_path.parent))
    return ok, output
