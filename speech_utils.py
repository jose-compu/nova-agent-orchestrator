"""
Preprocess long or grounded text into a short, natural phrase for TTS.
Uses Nova 2 Lite to summarize search/long output into 1-2 short spoken phrases.
"""
import re


def _strip_markdown_links(text: str) -> str:
    """Remove markdown-style links like (url) or [text](url)."""
    text = re.sub(r'\(https?://[^)]+\)', '', text)
    text = re.sub(r'\[([^\]]*)\]\([^)]+\)', r'\1', text)
    return text


def _strip_markdown_bold(text: str) -> str:
    return re.sub(r'\*\*([^*]+)\*\*', r'\1', text)


def _first_sentences(text: str, max_sentences: int = 2) -> str:
    text = text.strip()
    if not text:
        return ""
    parts = re.split(r'(?<=[.!?])\s+', text)
    return " ".join(parts[:max_sentences]).strip()


def summarize_for_speech(text: str, api_key: str | None = None, max_input_chars: int = 600) -> str:
    """
    Use Nova 2 Lite to summarize into 1-2 short spoken phrases. No bullets or links.
    """
    if not text or len(text.strip()) < 80:
        return text.strip()[:300]
    from nova_client import chat
    clean = _strip_markdown_links(_strip_markdown_bold(text))
    clean = re.sub(r'\s+', ' ', clean).strip()
    if len(clean) > max_input_chars:
        last_space = clean[:max_input_chars].rfind(' ')
        clean = clean[:last_space] if last_space > 0 else clean[:max_input_chars]
    try:
        r = chat(
            messages=[
                {"role": "system", "content": "You summarize text into one or two short spoken sentences. No bullet points, no links, no citations. Be conversational and brief. Output only the summary."},
                {"role": "user", "content": clean},
            ],
            max_tokens=80,
            api_key=api_key,
        )
        if r.choices and r.choices[0].message.content:
            out = r.choices[0].message.content.strip()
            return out[:300] if len(out) > 300 else out
    except Exception:
        pass
    return _first_sentences(clean, max_sentences=2)[:300]


def text_for_speech(text: str, max_chars: int = 180, use_ai_summary: bool = True, api_key: str | None = None) -> str:
    """
    Make text suitable for TTS: optionally AI-summarize, then strip and shorten.
    When use_ai_summary is True and text is long, uses Nova Lite to summarize into 1-2 phrases.
    """
    if not text or not text.strip():
        return ""
    s = text.strip()
    if use_ai_summary and len(s) > 100:
        s = summarize_for_speech(s, api_key=api_key, max_input_chars=500)
    else:
        s = _strip_markdown_links(s)
        s = _strip_markdown_bold(s)
        s = re.sub(r'\s+', ' ', s).strip()
        s = _first_sentences(s, max_sentences=2)
    if len(s) > max_chars:
        truncated = s[:max_chars]
        last_space = truncated.rfind(' ')
        s = truncated[:last_space] if last_space > 0 else truncated
    return s.strip()
