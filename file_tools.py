"""
Local file search (cached) and open file with default app.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
from pathlib import Path

# In-memory cache: key -> (paths, timestamp). File cache for persistence.
CACHE_FILE = Path(__file__).resolve().parent / ".file_search_cache.json"
CACHE_TTL_SEC = 3600  # 1 hour
MEMORY_CACHE: dict[str, tuple[list[str], float]] = {}
MAX_DEPTH = 8
# When scope is home dir, use smaller depth so the walk finishes in reasonable time.
MAX_DEPTH_HOME = 5
MAX_RESULTS = 80
DEFAULT_SCOPE = str(Path.home())

# Last search results for "open 2" style resolution (set by orchestrator after search).
last_search_results: list[str] = []

# ——— Shared filepaths cache (used by all tools: file_tools, skills_tools, orchestrator, etc.) ———
# Recently opened or search-result file paths (newest first), persisted. Any tool that opens,
# reads, or processes a file by path should call add_recent_path() so this list stays shared.
RECENT_PATHS_FILE = Path(__file__).resolve().parent / ".recent_file_paths.json"
RECENT_PATHS_MAX = 100
_recent_file_paths: list[str] = []


def _load_recent_paths() -> list[str]:
    try:
        if RECENT_PATHS_FILE.exists():
            with open(RECENT_PATHS_FILE, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [str(p) for p in data if p]
    except Exception:
        pass
    return []


def _save_recent_paths(paths: list[str]) -> None:
    try:
        with open(RECENT_PATHS_FILE, "w", encoding="utf-8") as f:
            json.dump(paths[:RECENT_PATHS_MAX], f, indent=0)
    except Exception:
        pass


def get_recent_file_paths() -> list[str]:
    """Return list of recently opened or search-result paths (newest first)."""
    global _recent_file_paths
    if not _recent_file_paths:
        _recent_file_paths = _load_recent_paths()
    return list(_recent_file_paths)


def add_recent_path(path: str) -> None:
    """Add a path to recent list (e.g. after open or from search results). Deduplicates and persists."""
    if not path or not path.strip():
        return
    p = str(Path(path).expanduser().resolve())
    global _recent_file_paths
    if not _recent_file_paths:
        _recent_file_paths = _load_recent_paths()
    if p in _recent_file_paths:
        _recent_file_paths.remove(p)
    _recent_file_paths.insert(0, p)
    _recent_file_paths = _recent_file_paths[:RECENT_PATHS_MAX]
    _save_recent_paths(_recent_file_paths)


def add_recent_paths(paths: list[str]) -> None:
    """Add multiple paths (e.g. from a file search). Newest-first order preserved."""
    for path in reversed(paths or []):
        add_recent_path(path)


def _query_case_variants(query: str) -> list[str]:
    """Return [lower, title, upper] for the query so find can match any casing."""
    q = query.strip()
    if not q:
        return []
    lower = q.lower()
    title = q.title()
    upper = q.upper()
    variants = [lower]
    if title != lower:
        variants.append(title)
    if upper != lower:
        variants.append(upper)
    return variants


def _cache_key(query: str, scope: str, extensions: tuple[str, ...]) -> str:
    return json.dumps({"q": query.strip().lower(), "scope": scope, "ext": sorted(extensions)})


def _load_file_cache() -> dict:
    try:
        if CACHE_FILE.exists():
            with open(CACHE_FILE, encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_file_cache(data: dict) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=0)
    except Exception:
        pass


def search_files(
    query: str = "",
    scope: str = DEFAULT_SCOPE,
    extensions: list[str] | None = None,
    max_results: int = MAX_RESULTS,
    max_depth: int = MAX_DEPTH,
    use_cache: bool = True,
) -> list[str]:
    """
    Search for files under scope. Optional query (substring in filename), optional extensions (e.g. [".mp3", ".m4a"]).
    Returns list of absolute paths, cached by (query, scope, extensions).
    """
    scope_path = Path(scope).expanduser().resolve()
    if not scope_path.is_dir():
        print("[file_tools] search_files scope is not a dir:", scope_path)
        return []
    ext_tuple = tuple((e if e.startswith(".") else f".{e}") for e in (extensions or []))
    key = _cache_key(query, str(scope_path), ext_tuple)
    print("[file_tools] search_files query:", repr(query), "scope:", scope_path, "extensions:", ext_tuple, flush=True)

    if use_cache:
        now = time.time()
        if key in MEMORY_CACHE:
            paths, ts = MEMORY_CACHE[key]
            if now - ts < CACHE_TTL_SEC and len(paths) > 0:
                print("[file_tools] search_files cache hit (memory), count:", len(paths), flush=True)
                return paths[:max_results]
        file_cache = _load_file_cache()
        if key in file_cache:
            entry = file_cache[key]
            if isinstance(entry, dict) and entry.get("ts", 0) + CACHE_TTL_SEC > now:
                paths = entry.get("paths", [])
                if len(paths) > 0:
                    MEMORY_CACHE[key] = (paths, now)
                    print("[file_tools] search_files cache hit (file), count:", len(paths), flush=True)
                    return paths[:max_results]

    query_lower = query.strip().lower()
    results: list[str] = []

    # Use shallower depth when searching the whole home dir.
    effective_depth = max_depth
    try:
        if scope_path.samefile(Path.home()):
            effective_depth = min(max_depth, MAX_DEPTH_HOME)
    except OSError:
        pass

    # Use Unix find for speed (no Python walk).
    find_cmd: list[str] = ["find", str(scope_path), "-maxdepth", str(effective_depth), "-type", "f"]
    if ext_tuple:
        if len(ext_tuple) == 1:
            find_cmd.extend(["-iname", f"*{ext_tuple[0]}"])
        else:
            find_cmd.append("(")
            for i, e in enumerate(ext_tuple):
                if i:
                    find_cmd.append("-o")
                find_cmd.extend(["-iname", f"*{e}"])
            find_cmd.append(")")
    if query_lower:
        variants = _query_case_variants(query_lower)
        if len(variants) == 1:
            find_cmd.extend(["-iname", f"*{query_lower}*"])
        else:
            find_cmd.append("(")
            for i, v in enumerate(variants):
                if i:
                    find_cmd.append("-o")
                find_cmd.extend(["-iname", f"*{v}*"])
            find_cmd.append(")")

    print("[file_tools] search_files find:", " ".join(find_cmd[:14]), flush=True)
    try:
        proc = subprocess.run(
            find_cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0 and proc.stderr:
            print("[file_tools] find stderr:", proc.stderr[:200], flush=True)
        raw = (proc.stdout or "").strip()
        results = [p for p in raw.splitlines() if p.strip()]
    except subprocess.TimeoutExpired:
        print("[file_tools] find timed out", flush=True)
    except Exception as e:
        print("[file_tools] find error:", e, flush=True)

    results.sort(key=str.lower)
    results = results[:max_results]
    print("[file_tools] search_files find result count:", len(results), flush=True)
    if results:
        MEMORY_CACHE[key] = (results, time.time())
        file_cache = _load_file_cache()
        file_cache[key] = {"paths": results, "ts": time.time()}
        _save_file_cache(file_cache)
        add_recent_paths(results[:25])
    return results


def open_file(path: str) -> tuple[bool, str]:
    """
    Open path with the system default application. Returns (success, message).
    On macOS, audio/video are opened with QuickTime Player and playback is started automatically.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return False, f"File not found: {p}"
    path_str = str(p)
    try:
        system = platform.system()
        if system == "Darwin":
            suffix = p.suffix.lower()
            audio_video = suffix in (".mp3", ".m4a", ".wav", ".aac", ".flac", ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".wmv")
            if audio_video:
                # Open in background (-g) then start playback and hide window so it stays in background
                subprocess.run(
                    ["open", "-g", "-a", "QuickTime Player", path_str],
                    check=True,
                    timeout=10,
                )
                time.sleep(1.2)
                try:
                    subprocess.run(
                        ["osascript", "-e", 'tell application "QuickTime Player" to tell front document to play'],
                        capture_output=True,
                        timeout=5,
                    )
                    subprocess.run(
                        ["osascript", "-e", 'tell application "QuickTime Player" to set visible to false'],
                        capture_output=True,
                        timeout=3,
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass
            else:
                subprocess.run(["open", path_str], check=True, timeout=10)
        elif system == "Linux":
            subprocess.run(["xdg-open", path_str], check=True, timeout=10)
        elif system == "Windows":
            os.startfile(path_str)
        else:
            return False, "Unsupported platform for open file."
        add_recent_path(path_str)
        return True, f"Opened: {path_str}"
    except subprocess.CalledProcessError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


def stop_playback() -> str:
    """
    Stop music/media playback by stopping all common players (Music, Spotify, VLC, QuickTime).
    Tries every app so playback stops regardless of which app was used to open the file.
    For VLC and QuickTime Player, falls back to killing the process if AppleScript does not work.
    """
    if platform.system() == "Darwin":
        scripts = [
            ("Music", 'tell application "Music" to stop'),
            ("Spotify", 'tell application "Spotify" to pause'),
            ("VLC", 'tell application "VLC" to stop'),
            ("QuickTime Player", 'tell application "QuickTime Player" to stop'),
        ]
        stopped = []
        for app_name, script in scripts:
            try:
                proc = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    timeout=5,
                )
                if proc.returncode == 0:
                    stopped.append(app_name)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
                continue
        # Ensure VLC is stopped: if AppleScript didn't report it, try killing the process
        if "VLC" not in stopped:
            try:
                kill_proc = subprocess.run(
                    ["killall", "VLC"],
                    capture_output=True,
                    timeout=3,
                )
                if kill_proc.returncode == 0:
                    stopped.append("VLC")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
        # Ensure QuickTime Player is stopped (used for songs): fallback to kill process
        if "QuickTime Player" not in stopped:
            try:
                kill_proc = subprocess.run(
                    ["killall", "QuickTime Player"],
                    capture_output=True,
                    timeout=3,
                )
                if kill_proc.returncode == 0:
                    stopped.append("QuickTime Player")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
        if stopped:
            return "Stopped: " + ", ".join(stopped) + "."
        return "No music or video app was playing, or they could not be stopped."
    if platform.system() == "Linux":
        try:
            proc = subprocess.run(["playerctl", "stop"], capture_output=True, timeout=5)
            if proc.returncode == 0:
                return "Playback stopped."
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return "Could not stop playback (playerctl not found or no player running)."
    return "Stop playback is not supported on this system."


def resolve_open_target(user_message: str) -> str | None:
    """
    If user says "open 2" or "play 1", return the path from last_search_results; else try to extract a path from the message.
    Also resolves "last", "recent", "open one", and matches by filename (e.g. "play the song from Two Steps from Hell").
    """
    msg = (user_message or "").strip()
    lower = msg.lower()
    # "open last", "open the last one", "open recent"
    if "last" in lower or "recent" in lower or "just opened" in lower:
        recent = get_recent_file_paths()
        if recent:
            return recent[0]
    if not last_search_results:
        # Try path in message
        for part in re.split(r"\s+", msg):
            part = part.strip(".,;\"'")
            if part.startswith("/") or part.startswith("~"):
                p = Path(part).expanduser().resolve()
                if p.exists():
                    return str(p)
        return None
    # Match "open 1", "play 2", "open the second one", "play first", "open one", "play one"
    idx = None
    if "first" in lower or "1st" in lower:
        idx = 0
    elif "second" in lower or "2nd" in lower:
        idx = 1
    elif "third" in lower or "3rd" in lower:
        idx = 2
    elif re.search(r"\b(?:open|play)\s+(?:the\s+)?one\b", lower):
        idx = 0
    elif re.search(r"\b(?:open|play)\s+(?:the\s+)?two\b", lower):
        idx = 1
    elif re.search(r"\b(?:open|play)\s+(?:the\s+)?three\b", lower):
        idx = 2
    if idx is not None and 0 <= idx < len(last_search_results):
        return last_search_results[idx]
    m = re.search(r"(?:open|play)\s+(?:the\s+)?(\d+)", msg, re.I)
    if m:
        try:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(last_search_results):
                return last_search_results[idx]
        except ValueError:
            pass
    # Match by filename: e.g. "play the song from Two Steps from Hell" -> path containing that name
    if last_search_results and any(
        w in lower for w in ("open", "play", "file", "song", "mp3", "music", "that", "this")
    ):
        stop = {"open", "play", "file", "song", "songs", "mp3", "music", "the", "a", "an", "from", "please", "that", "this", "one", "two", "three"}
        words = [w for w in re.split(r"[\s.,;]+", lower) if len(w) > 1 and w not in stop]
        if words:
            best_path = None
            best_score = 0
            for path in last_search_results:
                name = Path(path).name.lower()
                score = sum(1 for w in words if w in name)
                if score > best_score:
                    best_score = score
                    best_path = path
            if best_path and best_score > 0:
                return best_path
    for part in re.split(r"\s+", msg):
        part = part.strip(".,;\"'")
        if part.startswith("/") or part.startswith("~"):
            p = Path(part).expanduser().resolve()
            if p.exists():
                return str(p)
    return None
