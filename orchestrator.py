"""
Orchestrator: agent loop — tool selection → research / answer / plan / file tools → final text.
"""
from pathlib import Path
from typing import Any, Callable, Iterator

from file_tools import (
    get_recent_file_paths,
    last_search_results,
    open_file as open_file_tool,
    resolve_open_target,
    search_files,
    stop_playback as stop_playback_tool,
)
from modes import (
    get_memory,
    get_short_term,
    handle_mode_command,
    memory_context_for_search,
    memory_context_passive,
    extract_and_store_memory,
    remove_memory,
    update_memory,
)
from nova_client import chat, stream_chat
from research import research
from skill_to_python import convert_and_run as skill_convert_and_run
from skills_tools import (
    load_skill_content,
    format_skills_list,
    format_skills_list_speak_only,
    get_skill_root,
    search_skills as search_skills_tool,
)
from tool_selection import select_tool

ToolChoice = str  # "answer" | "web_search" | "plan_then_act" | "local_file_search" | "open_file"


def _parse_file_search_intent(user_message: str, api_key: str | None = None, memory_context: str | None = None) -> tuple[str, list[str]]:
    """Use Nova to get filename query and extensions from user message (and optional memory). Returns (query, extensions)."""
    prompt = """From the user message (and any context about the user's preferences), extract intent for searching files on their computer.
Reply with exactly two lines:
query: <words to match in file names, or "all" if any>
extensions: <comma-separated list, e.g. .mp3,.m4a for music; .doc,.docx for Word; .md for Markdown; .pdf for PDF; .txt for text; leave empty for all file types>

Examples:
- "MP3s" or "music files" -> query: all, extensions: .mp3,.m4a,.wav
- "Word documents" -> query: all, extensions: .doc,.docx
- "find the song I like" + context "user's favourite song is Touch the Sky" -> query: Touch the Sky, extensions: .mp3,.m4a,.wav
- "how many files in this project" -> query: all, extensions:
"""
    if memory_context:
        prompt += f"\nContext (stored facts about the user, use to interpret the request): {memory_context[:400]}\n\n"
    prompt += "User: "
    print("[orchestrator] _parse_file_search_intent calling Nova for:", repr(user_message[:80]))
    resp = chat(
        messages=[
            {"role": "user", "content": prompt + user_message},
        ],
        max_tokens=80,
        api_key=api_key,
    )
    query, exts = "all", []
    if resp.choices:
        content = (resp.choices[0].message.content or "").strip()
        print("[orchestrator] _parse_file_search_intent raw:", repr(content))
        for line in content.splitlines():
            line = line.strip()
            if line.lower().startswith("query:"):
                q = line[6:].strip().strip(".").lower()
                if q and q != "all":
                    query = q
            elif line.lower().startswith("extensions:"):
                raw = line[11:].strip()
                if raw:
                    parsed = [e.strip() if e.strip().startswith(".") else f".{e.strip()}" for e in raw.split(",")]
                    # Only keep extensions that look like real ones (e.g. .mp3, .doc); ignore "(empty for all)" etc.
                    exts = [e for e in parsed if e and len(e) >= 2 and e[1:].replace("_", "").isalnum()]
    # Fallback: if user message mentions file types and we got no extensions, set from keywords
    msg_lower = (user_message or "").lower()
    if not exts:
        if "mp3" in msg_lower or "music" in msg_lower or "song" in msg_lower or "songs" in msg_lower:
            exts = [".mp3", ".m4a", ".wav"]
        elif "word" in msg_lower or "doc " in msg_lower or "docx" in msg_lower or "document" in msg_lower:
            exts = [".doc", ".docx"]
        elif "markdown" in msg_lower or " .md" in msg_lower or "md file" in msg_lower:
            exts = [".md"]
        elif "pdf" in msg_lower:
            exts = [".pdf"]
        elif "text" in msg_lower and "file" in msg_lower:
            exts = [".txt"]
    print("[orchestrator] _parse_file_search_intent -> query:", repr(query), "extensions:", exts)
    return query, exts


def _parse_skill_search_query(user_message: str, api_key: str | None = None) -> str:
    """Extract skill search keywords from user message (e.g. 'search for skills to generate HTML' -> 'generate HTML')."""
    prompt = """From the user message, extract the topic or type of skills they want (e.g. HTML, PDF, testing).
Reply with one short phrase only, no punctuation. Examples:
- "search for skills to generate HTML pages" -> generate HTML
- "what skills do we have for documents" -> documents
- "list skills for web" -> web
User: """
    try:
        resp = chat(
            messages=[{"role": "user", "content": prompt + (user_message or "")[:200]}],
            max_tokens=30,
            api_key=api_key,
        )
        if resp.choices and resp.choices[0].message.content:
            return (resp.choices[0].message.content or "").strip().strip(".").strip() or user_message[:100]
    except Exception:
        pass
    return (user_message or "").strip()[:100]


def _parse_load_skill_intent(user_message: str, api_key: str | None = None) -> tuple[str, str]:
    """Extract (skill_search_terms, task_description) for load_skill. E.g. 'use HTML skill to make a table' -> ('html', 'make a table of ...')."""
    prompt = """From the user message, extract:
1) What kind of skill they want (one or two words: e.g. html, PDF, theme, web).
2) The task they want done (short phrase describing the output or goal).

Reply with exactly two lines:
skill: <word or two>
task: <short task description>
User: """
    try:
        resp = chat(
            messages=[{"role": "user", "content": prompt + (user_message or "")[:300]}],
            max_tokens=80,
            api_key=api_key,
        )
        if resp.choices and resp.choices[0].message.content:
            content = (resp.choices[0].message.content or "").strip()
            skill_q, task = "", user_message or ""
            for line in content.splitlines():
                line = line.strip()
                if line.lower().startswith("skill:"):
                    skill_q = line[6:].strip().strip(".").lower()
                elif line.lower().startswith("task:"):
                    task = line[5:].strip().strip(".").strip()
            if skill_q or task:
                return (skill_q or "html web", task)
    except Exception:
        pass
    return ("html web", (user_message or "").strip()[:200])


def _format_memory_for_context() -> str:
    """Short string of stored facts (long-term) and recent assistant facts (short-term) for tool selection and resolution."""
    items = get_memory()
    parts = []
    if items:
        parts.append(" ".join((it.get("fact") or "").strip() for it in items[:10] if (it.get("fact") or "").strip()))
    short = get_short_term()
    if short:
        parts.append("Recent from assistant: " + " | ".join(short[-5:]))  # last 5 short-term
    return " ".join(parts) if parts else ""


def _resolve_path_using_memory_and_recent(
    user_message: str,
    memory_context: str,
    recent_paths: list[str],
    api_key: str | None = None,
) -> str | None:
    """Use Nova to pick one path from recent_paths that matches user intent (user_message + memory)."""
    if not recent_paths or not (user_message or "").strip():
        return None
    paths_block = "\n".join(recent_paths[:20])
    prompt = f"""User said: "{user_message[:300]}"

Stored memories/preferences (use to interpret what they want): {memory_context[:500] if memory_context else "None"}

Recent file paths (pick the one that best matches the user's request, or reply NONE):
{paths_block}

Reply with exactly one line: either the full path to open, or the word NONE."""
    try:
        resp = chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            api_key=api_key,
        )
        if not resp.choices or not resp.choices[0].message.content:
            return None
        line = (resp.choices[0].message.content or "").strip().splitlines()[0].strip()
        if not line or line.upper() == "NONE":
            return None
        path = line.strip("'\"").strip()
        if path in recent_paths:
            return path
        for p in recent_paths:
            if path in p or p.endswith(path):
                return p
        return None
    except Exception:
        return None


def build_messages(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    system: str | None = None,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Build messages list for chat: optional system (with all memories as passive context), history, then user."""
    messages: list[dict[str, Any]] = []
    if system:
        memory_block = memory_context_passive()
        system = memory_block + system
        messages.append({"role": "system", "content": system})
    if history:
        for m in history:
            messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_message})
    return messages


def run_turn(
    user_message: str,
    *,
    api_key: str | None = None,
    on_status: Callable[[str], None] | None = None,
    stream: bool = True,
) -> str:
    """
    One agent turn: route → execute (research / answer / plan) → return final text.
    """
    def status(s: str) -> None:
        if on_status:
            on_status(s)

    mode_response = handle_mode_command(user_message)
    if mode_response is not None:
        return mode_response

    status("Choosing action...")
    memory_ctx = _format_memory_for_context()
    choice = select_tool(user_message, api_key=api_key, memory_context=memory_ctx)

    if choice == "store_memory":
        status("Storing memory.")
        return extract_and_store_memory(user_message, api_key=api_key)

    if choice == "remove_memory":
        status("Removing memory.")
        return remove_memory(user_message, api_key=api_key)

    if choice == "update_memory":
        status("Updating memory.")
        return update_memory(user_message, api_key=api_key)

    if choice == "search_skills":
        status("Searching skills.")
        query = _parse_skill_search_query(user_message, api_key=api_key)
        matches = search_skills_tool(query)
        return format_skills_list(matches)

    if choice == "load_skill":
        status("Loading skill and applying.")
        skill_query, task = _parse_load_skill_intent(user_message, api_key=api_key)
        matches = search_skills_tool(skill_query)
        if not matches:
            return "No matching skill found. Try \"search for skills to generate HTML\" to see available skills."
        content = load_skill_content(matches[0]["name"])
        if not content:
            return f"Could not load skill: {matches[0]['name']}."
        system = (
            "You are applying a Claude Code skill to the user's task. Use the following skill instructions as your guide. "
            "Output concrete steps, code, or content as needed. Be concise but complete.\n\n--- SKILL ---\n"
            + content[:12000]
            + "\n--- END SKILL ---\n\nUser task: "
            + task
        )
        resp = chat(
            messages=build_messages(task, system=system, api_key=api_key),
            max_tokens=1024,
            api_key=api_key,
        )
        if not resp.choices:
            return ""
        return (resp.choices[0].message.content or "").strip()

    if choice == "web_search":
        status("Searching the web.")
        search_context = memory_context_for_search()
        query = f"{search_context}\n\n{user_message}" if search_context else user_message
        result = research(query, api_key=api_key)
        if not result:
            status("Answering from knowledge.")
            resp = chat(
                messages=build_messages(
                    user_message,
                    system="Answer briefly based on general knowledge.",
                    api_key=api_key,
                ),
                max_tokens=256,
                api_key=api_key,
            )
            result = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        return result

    if choice == "plan_then_act":
        status("Planning steps.")
        plan_resp = chat(
            messages=build_messages(
                user_message,
                system="Suggest 1–3 short steps to answer. Be concise.",
                api_key=api_key,
            ),
            max_tokens=200,
            reasoning_effort="low",
            api_key=api_key,
        )
        plan_text = ""
        if plan_resp.choices:
            msg = plan_resp.choices[0].message
            plan_text = (msg.content or getattr(msg, "reasoning_content", "") or "").strip()
        status("Answering.")
        answer_resp = chat(
            messages=build_messages(
                user_message,
                history=[{"role": "assistant", "content": plan_text}] if plan_text else None,
                system="Answer the user concisely.",
                api_key=api_key,
            ),
            max_tokens=256,
            api_key=api_key,
        )
        answer = ""
        if answer_resp.choices:
            answer = (answer_resp.choices[0].message.content or "").strip()
        return answer or plan_text

    # answer
    status("Answering.")
    if stream:
        parts: list[str] = []
        for delta in stream_chat(
            build_messages(
                user_message,
                system="You are Nova, the user's assistant. Answer briefly and clearly. If asked your name or who you are, say you are Nova.",
                api_key=api_key,
            ),
            max_tokens=256,
            api_key=api_key,
        ):
            parts.append(delta)
        return "".join(parts).strip()
    resp = chat(
        messages=build_messages(
            user_message,
            system="You are Nova, the user's assistant. Answer briefly and clearly. If asked your name or who you are, say you are Nova.",
            api_key=api_key,
        ),
        max_tokens=256,
        api_key=api_key,
    )
    if not resp.choices:
        return ""
    return (resp.choices[0].message.content or "").strip()


def run_turn_stream(
    user_message: str,
    *,
    api_key: str | None = None,
    on_status: Callable[[str], None] | None = None,
) -> Iterator[str]:
    """Run one turn and stream the final answer text (no intermediate tool streaming)."""
    def status(s: str) -> None:
        if on_status:
            on_status(s)

    mode_response = handle_mode_command(user_message)
    if mode_response is not None:
        yield mode_response
        return

    if not (user_message or "").strip():
        yield "What would you like to know? Type a message and press Send."
        return

    status("Choosing action...")
    memory_ctx = _format_memory_for_context()
    choice = select_tool(user_message, api_key=api_key, memory_context=memory_ctx)
    print("[orchestrator] run_turn_stream choice:", choice)

    if choice == "store_memory":
        status("Storing memory.")
        yield extract_and_store_memory(user_message, api_key=api_key)
        return

    if choice == "remove_memory":
        status("Removing memory.")
        yield remove_memory(user_message, api_key=api_key)
        return

    if choice == "update_memory":
        status("Updating memory.")
        yield update_memory(user_message, api_key=api_key)
        return

    if choice == "search_skills":
        status("Searching skills.")
        query = _parse_skill_search_query(user_message, api_key=api_key)
        print("[orchestrator] search_skills query:", repr(query), flush=True)
        matches = search_skills_tool(query)
        # Speak only "I found N skills. 1. name. 2. name."; show full list with descriptions in chat (DISPLAY_ONLY)
        speak_line = format_skills_list_speak_only(matches)
        full_list = format_skills_list(matches)
        yield speak_line + "\n\n\0DISPLAY_ONLY\n\n" + full_list
        return

    if choice == "load_skill":
        status("Loading skill and applying.")
        skill_query, task = _parse_load_skill_intent(user_message, api_key=api_key)
        # Pass full user message to the script so it has context (e.g. "with the wine" or embedded JSON)
        task_for_script = (user_message or "").strip() or task
        print("[orchestrator] load_skill skill_query:", repr(skill_query), "task:", repr(task_for_script)[:80], flush=True)
        matches = search_skills_tool(skill_query)
        if not matches:
            yield "No matching skill found. Try \"search for skills to generate HTML\" to see available skills."
            return
        chosen = matches[0]
        content = load_skill_content(chosen["name"])
        if not content:
            yield f"Could not load skill: {chosen['name']}."
            return
        # Convert skill to minimal Python script, persist under skills-python/, run with task
        status("Converting skill to Python and running.")
        skill_root = get_skill_root(chosen["name"]) or get_skill_root(chosen["path"])
        ok, output = skill_convert_and_run(
            chosen["name"],
            content,
            task_for_script,
            api_key=api_key,
            skill_root=skill_root,
        )
        if ok and output:
            yield output
            return
        # Not suitable for Python: show message only, no fallback
        if not ok and output and output.strip().startswith("This skill is not suitable"):
            yield output
            return
        # Fallback: apply skill via Nova Lite (stream chat with skill as context)
        if not ok and output:
            yield f"Script run failed: {output}\n\nTrying assistant reply instead."
        status("Applying skill via assistant.")
        system = (
            "You are applying a Claude Code skill to the user's task. Use the following skill instructions as your guide. "
            "Output concrete steps, code, or content as needed. Be concise but complete.\n\n--- SKILL ---\n"
            + content[:12000]
            + "\n--- END SKILL ---\n\nUser task: "
            + task
        )
        for chunk in stream_chat(
            build_messages(task, system=system, api_key=api_key),
            max_tokens=1024,
            api_key=api_key,
        ):
            yield chunk
        return

    if choice == "local_file_search":
        status("Search local files.")
        memory_ctx = _format_memory_for_context()
        query, extensions = _parse_file_search_intent(user_message, api_key=api_key, memory_context=memory_ctx)
        scope = str(Path.home())
        print("[orchestrator] local_file_search scope:", scope, "query:", repr(query), "extensions:", extensions)
        paths = search_files(query=query if query != "all" else "", scope=scope, extensions=extensions or None)
        print("[orchestrator] local_file_search paths count:", len(paths), "first 3:", (paths[:3] if paths else []))
        last_search_results.clear()
        last_search_results.extend(paths)
        if not paths:
            yield "**Local search mode** — No matching files found. Try different keywords or another folder."
            return
        n = len(paths)
        label = "file" if n == 1 else "files"
        # Show summary + filenames in chat; TTS should only say "Local search mode" (see ui.py DISPLAY_ONLY_MARKER)
        yield "Local search mode\n\n\0DISPLAY_ONLY\n\n"
        yield f"Found {n} {label}. Say **Open 1** or **Open 2** to open one, or type a path.\n\n"
        for i, p in enumerate(paths[:25], 1):
            yield f"{i}. {Path(p).name}\n"
        if n > 25:
            yield f"\n… and {n - 25} more."
        return

    if choice == "open_file":
        status("Opening file.")
        path = resolve_open_target(user_message)
        if not path:
            memory_ctx = _format_memory_for_context()
            recent = get_recent_file_paths()
            path = _resolve_path_using_memory_and_recent(user_message, memory_ctx, recent, api_key=api_key)
        if not path and not last_search_results:
            status("Searching for files...")
            memory_ctx = _format_memory_for_context()
            query, extensions = _parse_file_search_intent(user_message, api_key=api_key, memory_context=memory_ctx)
            scope = str(Path.home())
            paths = search_files(query=query if query != "all" else "", scope=scope, extensions=extensions or None)
            last_search_results.clear()
            last_search_results.extend(paths)
            path = resolve_open_target(user_message) if paths else None
            if not path and paths:
                path = _resolve_path_using_memory_and_recent(user_message, memory_ctx, paths, api_key=api_key) or paths[0]
        if path:
            ok, msg = open_file_tool(path)
            yield msg if ok else f"Couldn’t open: {msg}"
        else:
            if last_search_results:
                yield "Say **Open 1**, **Open 2**, or the name (e.g. play the Two Steps from Hell song)."
            else:
                yield "Search for files first (e.g. find music or find MP3s), then say which to open or play."
        return

    if choice == "stop_playback":
        status("Stopping playback.")
        yield stop_playback_tool()
        return

    if choice == "web_search":
        if not (user_message or "").strip():
            yield "What would you like me to search for? Type your question and press Send."
            return
        status("Searching the web.")
        search_context = memory_context_for_search()
        query = f"{search_context}\n\n{user_message}" if search_context else user_message
        if search_context:
            print("[orchestrator] web_search with memory context:", repr(search_context[:120]), flush=True)
        result = research(query, api_key=api_key)
        if not result:
            status("Answering from knowledge.")
            for chunk in stream_chat(
                build_messages(user_message, system="Answer briefly.", api_key=api_key),
                max_tokens=256,
                api_key=api_key,
            ):
                yield chunk
            return
        yield result
        return

    if choice == "plan_then_act":
        status("Planning steps.")
        plan_resp = chat(
            messages=build_messages(user_message, system="1–3 short steps. Be concise.", api_key=api_key),
            max_tokens=200,
            reasoning_effort="low",
            api_key=api_key,
        )
        plan_text = ""
        if plan_resp.choices:
            msg = plan_resp.choices[0].message
            plan_text = (msg.content or getattr(msg, "reasoning_content", "") or "").strip()
        status("Answering.")
        for chunk in stream_chat(
            build_messages(
                user_message,
                history=[{"role": "assistant", "content": plan_text}] if plan_text else None,
                system="Answer concisely.",
                api_key=api_key,
            ),
            max_tokens=256,
            api_key=api_key,
        ):
            yield chunk
        return

    status("Answering.")
    for chunk in stream_chat(
        build_messages(
            user_message,
            system="You are Nova, the user's assistant. Answer briefly and clearly. If asked your name or who you are, say you are Nova.",
            api_key=api_key,
        ),
        max_tokens=256,
        api_key=api_key,
    ):
        yield chunk
