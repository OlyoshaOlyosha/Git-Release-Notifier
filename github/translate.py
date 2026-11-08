"""Free Google Translate endpoint client used to translate release texts."""

from __future__ import annotations

import logging

import aiohttp

logger = logging.getLogger(__name__)

_TRANSLATE_URL = "https://clients5.google.com/translate_a/t"
# Endpoint handles long texts, but split anyway to keep requests small and
# marker tokens (no spaces) away from hard cuts.
_MAX_CHUNK_LEN = 4500
_TIMEOUT = aiohttp.ClientTimeout(total=30)


def _split_chunks(text: str, limit: int = _MAX_CHUNK_LEN) -> list[str]:
    """Split ``text`` into chunks under ``limit`` chars, preferring paragraph breaks.

    Leading separators of each chunk are dropped; callers should join results
    with "\\n\\n" (chunks are cut at paragraph/line boundaries in practice).
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = max(rest.rfind("\n\n", 0, limit), rest.rfind("\n", 0, limit), rest.rfind(" ", 0, limit))
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip(" \n")
    chunks.append(rest)
    return chunks


async def translate_text(text: str, lang: str) -> str:
    """Translate ``text`` to ``lang`` via the free Google Translate endpoint.

    Chunks long texts; joins translated chunks with paragraph breaks. Returns
    the original text unchanged on any failure so callers degrade gracefully
    (the notification still goes out, just untranslated).
    """
    if not text or not lang:
        return text
    translated: list[str] = []
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            for chunk in _split_chunks(text):
                params = {"client": "dict-chrome-ex", "sl": "auto", "tl": lang, "q": chunk}
                async with session.get(_TRANSLATE_URL, params=params) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                # Shape: [["translated", "src_lang"]]; be tolerant to nesting variants.
                first = data[0]
                if first and isinstance(first[0], list):
                    translated.append("".join(seg[0] for seg in first if seg and seg[0]))
                else:
                    translated.append(first[0])
    except Exception as e:
        logger.warning("Translation to '%s' failed: %s", lang, e)
        return text
    return "\n\n".join(translated)
