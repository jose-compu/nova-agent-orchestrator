"""
Modes of operation: normal vs admin/special modes (e.g. Memory Mode).
Memory: stored as list of {about: "user"|"ai"|"other", fact: str} so we know who the info is about.
"""
from __future__ import annotations

import os
import json
import re

_MODE = "normal"  # "normal" | "memory"
_MEMORY_FILE = os.path.join(os.path.dirname(__file__), ".nova_memory.json")


def _normalize(s: str) -> str:
    return " ".join(s.lower().split()) if s else ""


def get_mode() -> str:
    return _MODE


def set_mode(mode: str) -> None:
    global _MODE
    _MODE = mode if mode in ("normal", "memory") else "normal"


def _load_memory_raw() -> list[dict]:
    """Load memory list; each item is {about: str, fact: str}. Migrate old list-of-strings format."""
    try:
        if os.path.isfile(_MEMORY_FILE):
            with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return []
            out = []
            for item in data:
                if isinstance(item, dict) and "fact" in item:
                    about = (item.get("about") or "other").lower()
                    if about not in ("user", "ai", "other"):
                        about = "other"
                    out.append({"about": about, "fact": str(item["fact"]).strip()})
                elif isinstance(item, str) and item.strip():
                    out.append({"about": "other", "fact": item.strip()})
            return out
    except Exception:
        pass
    return []


def _save_memory(items: list[dict]) -> None:
    try:
        with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)
    except Exception:
        pass


def get_memory() -> list[dict]:
    """Return list of {about: 'user'|'ai'|'other', fact: str}."""
    return _load_memory_raw()


def add_memory(fact: str, about: str = "other") -> None:
    fact = fact.strip()
    if not fact:
        return
    about = (about or "other").lower()
    if about not in ("user", "ai", "other"):
        about = "other"
    items = _load_memory_raw()
    entry = {"about": about, "fact": fact}
    if entry not in items and not any(i["fact"] == fact for i in items):
        items.append(entry)
        _save_memory(items)


def clear_memory() -> None:
    """Erase all stored memories."""
    _save_memory([])


# Short-term memory: in-memory only, last N facts from assistant responses.
_SHORT_TERM: list[str] = []
MAX_SHORT_TERM = 10


def add_short_term_from_response(response_text: str) -> None:
    """Extract a brief fact from the assistant response and append to short-term memory."""
    if not response_text or not isinstance(response_text, str):
        return
    s = response_text.strip()
    if "\0DISPLAY_ONLY" in s:
        s = s.split("\0DISPLAY_ONLY")[0].strip()
    s = " ".join(s.split())
    if len(s) < 3:
        return
    first_sentence = s.split(".")[0].strip()
    fact = (first_sentence + ".") if first_sentence and not first_sentence.endswith(".") else (first_sentence or s[:120].strip())
    fact = fact[:120].strip()
    if not fact:
        return
    global _SHORT_TERM
    _SHORT_TERM.append(fact)
    if len(_SHORT_TERM) > MAX_SHORT_TERM:
        _SHORT_TERM = _SHORT_TERM[-MAX_SHORT_TERM:]


def get_short_term() -> list[str]:
    """Return current short-term memory (recent assistant-response facts). Not persisted."""
    return list(_SHORT_TERM)


def _find_memory_index(user_message: str, items: list[dict], action: str, api_key: str | None = None) -> int | None:
    """Use Nova to pick which memory (1-based index) the user means. Returns 1-based index or None."""
    if not items:
        return None
    from nova_client import chat
    numbered = "\n".join(f"{i + 1}. {it['fact']}" for i, it in enumerate(items))
    prompt = f"""Stored memories:
{numbered}

The user said: "{user_message[:300]}"

Which memory number (1 to {len(items)}) does the user want to {action}? Reply with only that number, or 0 if none."""
    try:
        r = chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=5,
            api_key=api_key,
        )
        if not r.choices or not r.choices[0].message.content:
            return None
        content = (r.choices[0].message.content or "").strip()
        for part in content.replace(",", " ").split():
            try:
                n = int(part)
                if 1 <= n <= len(items):
                    return n
                if n == 0:
                    return None
            except ValueError:
                pass
    except Exception:
        pass
    return None


def remove_memory(user_message: str, api_key: str | None = None) -> str:
    """Find the memory the user wants to remove, delete it, return status message."""
    items = _load_memory_raw()
    if not items:
        return "No memories stored."
    idx = _find_memory_index(user_message, items, "remove", api_key=api_key)
    if idx is None:
        return "Could not identify which memory to remove. Say e.g. 'remove the memory about Paris' or 'forget that I live in Paris'."
    removed = items.pop(idx - 1)
    _save_memory(items)
    return f"Removed memory: {removed.get('fact', '')}"


def update_memory(user_message: str, api_key: str | None = None) -> str:
    """Find the memory the user wants to update and the new fact, update it, return status message."""
    items = _load_memory_raw()
    if not items:
        return "No memories stored."
    from nova_client import chat
    numbered = "\n".join(f"{i + 1}. {it['fact']}" for i, it in enumerate(items))
    prompt = f"""Stored memories:
{numbered}

The user said: "{user_message[:350]}"

Which memory should be updated (reply with the number 1-{len(items)}), and what is the new fact? Reply with exactly two lines:
number: <1-{len(items)}>
new: <new fact text>"""
    try:
        r = chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            api_key=api_key,
        )
        if not r.choices or not r.choices[0].message.content:
            return "Could not parse which memory to update."
        content = (r.choices[0].message.content or "").strip()
        num, new_fact = None, ""
        for line in content.splitlines():
            line = line.strip()
            if line.lower().startswith("number:"):
                try:
                    num = int(line[7:].strip())
                except ValueError:
                    pass
            elif line.lower().startswith("new:"):
                new_fact = line[4:].strip()
        if num is None or not (1 <= num <= len(items)) or not new_fact:
            return "Could not identify which memory to update or the new value. Try e.g. 'change my city to London' or 'update the memory about Paris to London'."
        about = items[num - 1].get("about") or "other"
        items[num - 1] = {"about": about, "fact": new_fact}
        _save_memory(items)
        return f"Updated memory to: {new_fact}"
    except Exception:
        return "Could not update memory."


def _format_fact(item: dict) -> str:
    about = item.get("about") or "other"
    fact = item.get("fact") or ""
    if about == "user":
        return f"[ABOUT THE USER - the person chatting with you]: {fact}"
    if about == "ai":
        return f"[ABOUT YOU THE ASSISTANT - not the user]: {fact}"
    return f"[GENERAL]: {fact}"


def extract_and_store_memory(user_message: str, api_key: str | None = None) -> str:
    """
    Use Nova to extract the fact and who it's about (user, AI, or other). Store and return one clear line.
    Rejects meta-facts (e.g. "X is informing Nova about Y") and stores only the actual fact (e.g. "User's name is Pedro").
    """
    from nova_client import chat

    def _normalize_fact(raw: str) -> str:
        s = raw.strip()
        s = re.sub(r"^Storing\s+New\s+Memory\s*:\s*", "", s, flags=re.I)
        s = s.strip()
        first = s.split(".")[0].strip() if "." in s else s.split("\n")[0].strip()
        return first or s

    def _is_meta_fact(s: str) -> bool:
        """True if this describes the act of telling/informing rather than the fact itself."""
        lower = s.lower()
        if re.search(r"\b(informing|telling|said that|mentioned that|is telling|is informing|informs?|tells?)\b", lower):
            return True
        if re.search(r"\b(user|pedro|they)\s+(is|are)\s+(informing|telling|saying)\b", lower):
            return True
        return False

    try:
        r = chat(
            messages=[
                {"role": "system", "content": """Extract the single fact the user wants to remember. Store ONLY the actual fact, not a description of the act of telling.
BAD (meta): "Pedro is informing Nova about their names" / "The user said that they like X"
GOOD (fact): "The user's name is Pedro" / "The user likes X"
Reply with exactly two lines:
fact: <the actual fact in one short sentence, e.g. "The user's name is Pedro" or "The user lives in Paris">
about: USER or AI or OTHER"""},
                {"role": "user", "content": user_message},
            ],
            max_tokens=100,
            api_key=api_key,
        )
        if r.choices and r.choices[0].message.content:
            content = (r.choices[0].message.content or "").strip()
            fact_raw, about = "", "other"
            for line in content.splitlines():
                line = line.strip()
                if line.lower().startswith("fact:"):
                    fact_raw = _normalize_fact(line[5:].strip())
                elif line.lower().startswith("about:"):
                    a = line[6:].strip().upper()
                    if "USER" in a:
                        about = "user"
                    elif "AI" in a or "ASSISTANT" in a:
                        about = "ai"
                    else:
                        about = "other"
            if fact_raw and not _is_meta_fact(fact_raw):
                add_memory(fact_raw, about=about)
                who = {"user": "the user", "ai": "the AI", "other": "general"}[about]
                return f"Storing New Memory (about {who}): {fact_raw}"
            if fact_raw and _is_meta_fact(fact_raw):
                # Fallback: derive fact from user message (e.g. "remember my name is Pedro" -> "The user's name is Pedro")
                derived = _derive_fact_from_message(user_message)
                if derived and not _is_meta_fact(derived):
                    add_memory(derived, about="user")
                    return f"Storing New Memory (about the user): {derived}"
    except Exception:
        pass
    fact = _normalize_fact(user_message.strip()[:200])
    if fact and not _is_meta_fact(fact):
        add_memory(fact, about="other")
        return f"Storing New Memory (general): {fact}"
    derived = _derive_fact_from_message(user_message)
    if derived and not _is_meta_fact(derived):
        add_memory(derived, about="user")
        return f"Storing New Memory (about the user): {derived}"
    return "Could not extract memory."


def _derive_fact_from_message(user_message: str) -> str:
    """Heuristic: turn 'remember my name is Pedro' into 'The user's name is Pedro'."""
    msg = (user_message or "").strip()
    if not msg:
        return ""
    lower = msg.lower()
    if re.search(r"\bmy name is\s+(.+)", lower):
        m = re.search(r"\bmy name is\s+(.+)", lower, re.I)
        if m:
            name = m.group(1).strip().split()[0].strip(".,")
            return f"The user's name is {name}"
    if re.search(r"\bi (?:live|am) in\s+(.+)", lower):
        m = re.search(r"\bi (?:live|am) in\s+(.+)", lower, re.I)
        if m:
            place = m.group(1).strip().split(".")[0].strip(".,")
            return f"The user lives in {place}"
    if re.search(r"\bi like\s+(.+)", lower):
        m = re.search(r"\bi like\s+(.+)", lower, re.I)
        if m:
            thing = m.group(1).strip().split(".")[0].strip(".,")
            return f"The user likes {thing}"
    return ""


def memory_context_block() -> str:
    """Format all memories with clear 'About X' labels."""
    items = get_memory()
    if not items:
        return ""
    return "Stored facts (use only when relevant; note who each is about):\n- " + "\n- ".join(_format_fact(i) for i in items) + "\n\n"


def memory_context_passive() -> str:
    """Format ALL memories as passive context. Explicit: Nova = assistant only, never the user."""
    items = get_memory()
    short = get_short_term()
    parts = []
    if items:
        lines = "\n- ".join(_format_fact(i) for i in items)
        parts.append(
            "Passive stored facts (use only when the user's message clearly asks about that topic; otherwise do not mention):\n"
            "CRITICAL: Names/facts under [ABOUT YOU THE ASSISTANT] refer to you (the bot). Never use them for the user. "
            "If the user asks 'what is your name', answer with YOUR name (the assistant's name from the facts below). "
            "Never call or greet the user as Nova or by the assistant's name.\n- "
            + lines
        )
    if short:
        parts.append("Recent from this conversation (what you said or did): " + " | ".join(short[-5:]))
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n\n"


def memory_context_for_search() -> str:
    """
    Short context string for web search: user-related facts (location, preferences, etc.)
    so search results can be personalized. Excludes AI-only facts.
    """
    items = get_memory()
    if not items:
        return ""
    parts = []
    for it in items:
        about = (it.get("about") or "other").lower()
        if about == "ai":
            continue
        fact = (it.get("fact") or "").strip()
        if not fact:
            continue
        if about == "user":
            parts.append(f"User: {fact}")
        else:
            parts.append(fact)
    if not parts:
        return ""
    return "Context (use for location/preferences when relevant): " + "; ".join(parts[:5]) + "."


def get_relevant_memory_context(user_message: str, api_key: str | None = None) -> str:
    """
    Return only memories relevant to the question. First use heuristics to detect if the
    question is about the user, the AI, or other; filter by that. Then format so the model
    uses them (e.g. "When answering about the AI, use these facts: ...").
    """
    items = get_memory()
    if not items:
        return ""

    msg_lower = _normalize(user_message)

    # Heuristic: what is the question about?
    question_about = None
    if re.search(r"\b(your name|who are you|what are you|what'?s your name|your role|you called|your identity)\b", msg_lower):
        question_about = "ai"
    elif re.search(r"\b(my name|who am i|what'?s my name|my (preference|favorite|city|country))\b", msg_lower):
        question_about = "user"

    if question_about:
        filtered = [it for it in items if (it.get("about") or "other") == question_about]
        if filtered:
            who = "the AI/assistant" if question_about == "ai" else "the user"
            lines = "\n- ".join(_format_fact(it) for it in filtered)
            return f"The user is asking about {who}. Answer using these stored facts (use them as the primary answer):\n- {lines}\n\n"

    # Fallback: single fact
    if len(items) == 1:
        from nova_client import chat
        try:
            r = chat(
                messages=[
                    {"role": "user", "content": f'User asked: "{user_message[:300]}"\nStored fact (about {items[0]["about"]}): "{items[0]["fact"]}"\nIs this fact relevant to answering? Reply only YES or NO.'},
                ],
                max_tokens=5,
                api_key=api_key,
            )
            if r.choices and r.choices[0].message.content:
                if "YES" in (r.choices[0].message.content or "").strip().upper():
                    return "Relevant stored fact (use as the primary answer when it fits):\n- " + _format_fact(items[0]) + "\n\n"
        except Exception:
            pass
        return ""

    # Multiple facts: ask which are relevant, with clear hint about user vs AI
    from nova_client import chat
    try:
        numbered = "\n".join(f"{i + 1}. {_format_fact(it)}" for i, it in enumerate(items))
        prompt = f'''User asked: "{user_message[:400]}"

Stored facts (each labeled who it is about - "the user", "the AI/assistant", or "general"):
{numbered}

If the question is about the AI (e.g. "what is your name"), pick facts about the AI/assistant. If about the user (e.g. "what is my name"), pick facts about the user. Reply with only the relevant fact numbers, comma-separated (e.g. 2,3), or NONE if none apply.'''
        r = chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50,
            api_key=api_key,
        )
        if not r.choices or not r.choices[0].message.content:
            return ""
        content = (r.choices[0].message.content or "").strip()
        if "NONE" in content.upper():
            return ""
        relevant = []
        for part in content.replace(",", " ").split():
            try:
                i = int(part)
                if 1 <= i <= len(items):
                    relevant.append(items[i - 1])
            except ValueError:
                pass
        if not relevant:
            return ""
        return "Relevant stored facts (use as the primary answer when they fit the question):\n- " + "\n- ".join(_format_fact(it) for it in relevant) + "\n\n"
    except Exception:
        return ""


def handle_mode_command(message: str) -> str | None:
    """
    Mode triggers and memory commands.
    - "memory mode" / "exit memory mode"
    - "erase memories" / "clear memory" -> clear all, return "Memories cleared."
    """
    global _MODE
    msg = _normalize(message)
    if msg == "memory mode":
        set_mode("memory")
        return "Entering Memory Mode"
    if msg in ("exit memory mode", "exiting memory mode"):
        set_mode("normal")
        return "Exiting Memory Mode"
    if msg in ("erase memories", "erase memory", "clear memories", "clear memory"):
        clear_memory()
        return "Memories cleared."
    if get_mode() == "memory":
        add_memory(message.strip(), about="other")
        return f"Storing New Memory (general): {message.strip()}"
    return None
