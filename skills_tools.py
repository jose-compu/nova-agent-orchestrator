"""
Skills tools: search and load Claude Code skills from the project skills folder.
"""
import re
from pathlib import Path

from file_tools import add_recent_path

SKILLS_DIR = Path(__file__).resolve().parent / "skills"
SKILL_FILENAME = "SKILL.md"


def _parse_frontmatter(content: str) -> dict[str, str]:
    """Extract YAML frontmatter (name, description) from SKILL.md content."""
    out = {}
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return out
    block = match.group(1)
    for line in block.splitlines():
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            key, val = key.strip().lower(), val.strip().strip("'\"").strip()
            if key in ("name", "description"):
                out[key] = val
    return out


def get_skill_root(path_or_name: str) -> Path | None:
    """
    Return the directory that contains SKILL.md for this skill (the skill root).
    path_or_name can be skill name (e.g. 'docx') or path relative to SKILLS_DIR (e.g. 'skills/docx/SKILL.md').
    """
    if not path_or_name or not path_or_name.strip():
        return None
    path_or_name = path_or_name.strip()
    # By path
    candidate = SKILLS_DIR / path_or_name
    if candidate.is_file():
        return candidate.resolve().parent
    if (candidate / SKILL_FILENAME).is_file():
        return candidate.resolve()
    # By name
    for s in get_all_skills():
        if s["name"].lower() == path_or_name.lower():
            full = SKILLS_DIR / s["path"]
            if full.is_file():
                return full.resolve().parent
        if path_or_name.lower() in s["name"].lower():
            full = SKILLS_DIR / s["path"]
            if full.is_file():
                return full.resolve().parent
    return None


def find_skill_entrypoint(skill_root: Path) -> Path | None:
    """Return path to run.py or main.py in skill root or scripts/, or None."""
    if not skill_root or not skill_root.is_dir():
        return None
    for name in ("run.py", "main.py"):
        p = skill_root / name
        if p.is_file():
            return p
    scripts_run = skill_root / "scripts" / "run.py"
    if scripts_run.is_file():
        return scripts_run
    scripts_main = skill_root / "scripts" / "main.py"
    if scripts_main.is_file():
        return scripts_main
    return None


def get_all_skills() -> list[dict[str, str]]:
    """Scan SKILLS_DIR for all SKILL.md files; return list of {name, description, path}."""
    results = []
    if not SKILLS_DIR.is_dir():
        return results
    for path in SKILLS_DIR.rglob(SKILL_FILENAME):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            meta = _parse_frontmatter(text)
            name = meta.get("name") or path.parent.name
            desc = meta.get("description") or ""
            results.append({
                "name": name,
                "description": desc,
                "path": str(path.relative_to(SKILLS_DIR)),
            })
        except Exception:
            continue
    return sorted(results, key=lambda x: (x["name"].lower(), x["path"]))


# Query expansion: user term -> extra terms that indicate the right skill (e.g. spreadsheet -> xlsx)
SKILL_QUERY_SYNONYMS: dict[str, list[str]] = {
    "spreadsheet": ["xlsx", "excel", "csv"],
    "spreadsheets": ["xlsx", "excel", "csv"],
    "excel": ["xlsx", "spreadsheet"],
    "word": ["docx", "document"],
    "document": ["docx", "word"],
}


def search_skills(query: str) -> list[dict[str, str]]:
    """
    Search skills by name and description. Query is split into words; a skill matches if
    any word (or synonym) appears in name or description. Results are ranked: name match first,
    then description match. Skills that only mention the term in a "do not use" context are demoted.
    """
    if not query or not query.strip():
        return get_all_skills()
    all_skills = get_all_skills()
    words = [w.lower() for w in query.strip().split() if len(w) > 1]
    if not words:
        return all_skills
    # Expand with synonyms so e.g. "spreadsheet" also matches skill name "xlsx"
    search_terms = set(words)
    for w in words:
        for syn in SKILL_QUERY_SYNONYMS.get(w, []):
            search_terms.add(syn.lower())
    out = []
    for s in all_skills:
        name_lower = s["name"].lower()
        desc_lower = (s.get("description") or "").lower()
        combined = f"{name_lower} {desc_lower}"
        if not any(term in combined for term in search_terms):
            continue
        # Prefer name match over description-only match; demote "do not use for X" when X matches
        name_match = any(term in name_lower for term in search_terms)
        desc_match = any(term in desc_lower for term in search_terms)
        do_not_match = False
        if desc_match and not name_match:
            do_not_pattern = re.compile(
                r"do\s+not\s+use[^.]*?\b(" + "|".join(re.escape(t) for t in search_terms) + r")\b",
                re.I,
            )
            if do_not_pattern.search(desc_lower):
                do_not_match = True
        out.append({
            **s,
            "_name_match": name_match,
            "_do_not_match": do_not_match,
        })
    # Rank: name match first, then no "do not" match, then rest
    out.sort(key=lambda x: (x["_do_not_match"], not x["_name_match"], x["name"].lower()))
    return [{k: v for k, v in s.items() if not k.startswith("_")} for s in out]


def load_skill_content(path_or_name: str) -> str | None:
    """
    Load full content of a skill by path (relative to skills dir) or by name.
    Returns file content or None if not found. Uses shared filepaths cache (adds path on load).
    """
    if not path_or_name or not path_or_name.strip():
        return None
    path_or_name = path_or_name.strip()
    full_path: Path | None = None
    # Try as path first
    candidate = SKILLS_DIR / path_or_name
    if candidate.is_file():
        full_path = candidate.resolve()
    elif (candidate / SKILL_FILENAME).is_file():
        full_path = (candidate / SKILL_FILENAME).resolve()
    if full_path is not None:
        add_recent_path(str(full_path))
        return full_path.read_text(encoding="utf-8", errors="replace")
    # Search by name
    for s in get_all_skills():
        if s["name"].lower() == path_or_name.lower():
            full = SKILLS_DIR / s["path"]
            if full.is_file():
                full_path = full.resolve()
                add_recent_path(str(full_path))
                return full_path.read_text(encoding="utf-8", errors="replace")
    # Partial name match
    path_or_name_lower = path_or_name.lower()
    for s in get_all_skills():
        if path_or_name_lower in s["name"].lower():
            full = SKILLS_DIR / s["path"]
            if full.is_file():
                full_path = full.resolve()
                add_recent_path(str(full_path))
                return full_path.read_text(encoding="utf-8", errors="replace")
    return None


def format_skills_list(skills: list[dict[str, str]]) -> str:
    """Format search results for display: 'I found N skills: 1. name - description, ...'"""
    if not skills:
        return "No matching skills found."
    lines = [f"I found {len(skills)} skill(s):"]
    for i, s in enumerate(skills, 1):
        desc = (s.get("description") or "")[:120]
        if len((s.get("description") or "")) > 120:
            desc += "..."
        lines.append(f"{i}. **{s['name']}** — {desc}")
    return "\n\n".join(lines)


def format_skills_list_speak_only(skills: list[dict[str, str]]) -> str:
    """Short line for TTS: 'I found N skills. 1. name. 2. name.' — no descriptions."""
    if not skills:
        return "No matching skills found."
    names = ". ".join(f"{i}. {s['name']}" for i, s in enumerate(skills, 1))
    return f"I found {len(skills)} skill(s). {names}."
