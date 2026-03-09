"""
Research: Nova 2 Lite with nova_grounding for web search.
"""
import re
import time
from nova_client import chat

RESEARCH_SYSTEM = (
    "Reply in a few short lines only. Use very short bullets or 2–3 word phrases per item. "
    "No URLs, no links, no citations, no markdown. "
    "No long descriptions—one brief line per recommendation (e.g. 'Candelaria: creative tacos, good cocktails'). "
    "Maximum 8–10 bullets total. Be concise and scannable."
)

# In-memory cache for recent web searches: key -> (result_text, timestamp)
_SEARCH_CACHE: dict[str, tuple[str, float]] = {}
_SEARCH_CACHE_TTL_SEC = 1800  # 30 min
_SEARCH_CACHE_MAX = 50


def _search_cache_key(query: str) -> str:
    return (query or "").strip()[:500]


def research(
    query: str,
    max_tokens: int = 400,
    api_key: str | None = None,
) -> str:
    """Run web search / grounding via Nova 2 Lite; return full summarized response. Results are cached."""
    key = _search_cache_key(query)
    now = time.time()
    if key in _SEARCH_CACHE:
        text, ts = _SEARCH_CACHE[key]
        if now - ts < _SEARCH_CACHE_TTL_SEC:
            return text
    resp = chat(
        messages=[
            {"role": "system", "content": RESEARCH_SYSTEM},
            {"role": "user", "content": query},
        ],
        max_tokens=max_tokens,
        system_tools=["nova_grounding"],
        api_key=api_key,
    )
    if not resp.choices:
        return ""
    text = (resp.choices[0].message.content or "").strip()
    # Strip URLs, markdown links, and parenthetical domain citations
    text = re.sub(r'\[([^\]]*)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'\s*\([^)]*\.(?:com|org|net|fr)[^)]*\)', '', text, flags=re.I)
    text = re.sub(r'\s+\|\s*$', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    # Store in cache; evict oldest if over limit
    while len(_SEARCH_CACHE) >= _SEARCH_CACHE_MAX and _SEARCH_CACHE:
        oldest_key = min(_SEARCH_CACHE, key=lambda k: _SEARCH_CACHE[k][1])
        del _SEARCH_CACHE[oldest_key]
    _SEARCH_CACHE[key] = (text, now)
    return text
