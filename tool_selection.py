"""
Tool selection: single Nova 2 Lite call to choose answer | web_search | plan_then_act | store_memory.
"""
from typing import Literal

from nova_client import chat, get_client

ToolChoice = Literal["answer", "web_search", "plan_then_act", "store_memory", "remove_memory", "update_memory", "local_file_search", "open_file", "search_skills", "load_skill", "stop_playback"]
VALID_CHOICES: frozenset[str] = frozenset({"answer", "web_search", "plan_then_act", "store_memory", "remove_memory", "update_memory", "local_file_search", "open_file", "search_skills", "load_skill", "stop_playback"})

SYSTEM_PROMPT = """You are a router. Given the user message, output exactly one word: answer, web_search, plan_then_act, store_memory, remove_memory, update_memory, local_file_search, open_file, search_skills, load_skill, or stop_playback.
- answer: simple question, greeting, or when no search or multi-step plan is needed.
- web_search: user wants current info, news, weather, or to search the web.
- plan_then_act: user wants a multi-step plan or complex task.
- store_memory: user wants to save a preference, fact, or something to remember (e.g. "remember that...", "I only use X", "store this").
- remove_memory: user wants to forget or remove one stored fact (e.g. "forget that...", "remove the memory about X", "delete the memory about Paris").
- update_memory: user wants to change one stored fact (e.g. "change my city to London", "update the memory about X to Y", "I now live in Berlin").
- local_file_search: user wants to find, count, or list files on their computer (e.g. how many files, files in this project, search for MP3s, music, Word documents, Markdown files, PDFs, .doc, .md, .mp3, list documents).
- open_file: user wants to open or play a specific file (play that song, open the first one, open file at path, play number 2).
- search_skills: user wants to find or list Claude Code skills (e.g. "search for skills to generate HTML", "what skills do we have for X", "list skills for...").
- load_skill: user wants to use a skill to do a task (e.g. "use the HTML skill to generate...", "load the skill for X and do Y", "generate an HTML table using the skill").
- stop_playback: user wants to stop or pause music, song, or media playback (e.g. "stop the music", "stop the song", "stop playing", "pause playback", "stop that").
Output only the single word, nothing else."""


def parse_tool_response(raw: str) -> ToolChoice:
    """Parse model output to one of answer, web_search, plan_then_act, store_memory, local_file_search, open_file."""
    s = (raw or "").strip().lower()
    first = s.split()[0] if s.split() else s
    if first in VALID_CHOICES:
        return first  # type: ignore
    if "local_file_search" in s or "file_search" in s or "local search" in s or "find file" in s:
        return "local_file_search"
    if "search_skills" in s or "search for skill" in s or "skills to " in s or "list skill" in s or "find skill" in s:
        return "search_skills"
    if "load_skill" in s or "load skill" in s or "use the skill" in s or "use a skill" in s or "apply skill" in s:
        return "load_skill"
    if "file" in s and ("project" in s or "count" in s or "how many" in s or "list " in s or "find" in s):
        return "local_file_search"
    if "mp3" in s or "markdown" in s or " word " in s or "documents" in s or "pdf" in s or " .md " in s or " .doc" in s:
        return "local_file_search"
    if "open_file" in s or "open file" in s or "play file" in s:
        return "open_file"
    if "stop_playback" in s or "stop playback" in s or "stop music" in s or "stop song" in s or "stop playing" in s or "pause playback" in s:
        return "stop_playback"
    if "remove_memory" in s or "remove memory" in s or "forget" in s or "delete memory" in s:
        return "remove_memory"
    if "update_memory" in s or "update memory" in s or "change memory" in s or "change my" in s:
        return "update_memory"
    if "web_search" in s or "web search" in s or "search" in s:
        return "web_search"
    if "plan" in s or "plan_then_act" in s:
        return "plan_then_act"
    if "store_memory" in s or "memory" in s or "store" in s:
        return "store_memory"
    return "answer"


def select_tool(user_message: str, api_key: str | None = None, memory_context: str | None = None) -> ToolChoice:
    """Call Nova 2 Lite to select next tool; optional memory_context and recent_paths_summary improve routing."""
    print("[tool_selection] user_message:", repr(user_message[:120]))
    content = user_message
    if memory_context:
        content = content + "\n\n[Context – stored facts about the user, use to interpret intent: " + memory_context[:300] + "]"
    resp = chat(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        max_tokens=10,
        api_key=api_key,
    )
    if not resp.choices:
        print("[tool_selection] no choices, defaulting to answer")
        return "answer"
    raw_content = resp.choices[0].message.content or ""
    choice = parse_tool_response(raw_content)
    print("[tool_selection] raw:", repr(raw_content), "-> choice:", choice)
    return choice
