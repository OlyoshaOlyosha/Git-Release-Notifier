"""Tests for github.translate: chunking, translation via mocked endpoint, failure fallback."""

import github.translate as tr
from github.translate import _split_chunks, translate_text


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")

    async def json(self):
        return self._payload


class _FakeSession:
    """Returns one queued payload per GET, in order."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def get(self, url, params=None):
        self.requests.append((url, params))
        payload = self._payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return _FakeContext(payload)


class _FakeContext:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return _FakeResponse(self._payload)

    async def __aexit__(self, *exc):
        return None


def _payload(translated):
    # Real endpoint shape: [[["translated", "original", ...], ...], ...]
    return [[[translated, "orig", None, None]]]


# ---------------------------------------------------------------------------
# _split_chunks
# ---------------------------------------------------------------------------


def test_split_chunks_short_text_untouched():
    assert _split_chunks("hello") == ["hello"]


def test_split_chunks_prefers_paragraph_breaks():
    text = "a\n\n" * 2000  # 6000 chars, cuts at "\n\n"
    chunks = _split_chunks(text, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    # Chunking only re-shuffles whitespace: the content tokens must survive in order.
    assert " ".join(chunks).split() == text.split()


def test_split_chunks_hard_cut_without_separators():
    text = "x" * 250
    chunks = _split_chunks(text, limit=100)
    assert "".join(chunks) == text
    assert len(chunks) == 3


# ---------------------------------------------------------------------------
# translate_text
# ---------------------------------------------------------------------------


async def test_translate_text_empty_input(monkeypatch):
    assert await translate_text("", "ru") == ""
    assert await translate_text("hi", "") == "hi"


async def test_translate_text_success(monkeypatch):
    session = _FakeSession([_payload("привет")])
    monkeypatch.setattr(tr.aiohttp, "ClientSession", lambda timeout=None: session)
    result = await translate_text("hello", "ru")
    assert result == "привет"
    url, params = session.requests[0]
    assert url == tr._TRANSLATE_URL
    assert params["tl"] == "ru" and params["client"] == "dict-chrome-ex"


async def test_translate_text_joins_chunks(monkeypatch):
    session = _FakeSession([_payload("часть1"), _payload("часть2")])
    monkeypatch.setattr(tr.aiohttp, "ClientSession", lambda timeout=None: session)
    text = "a\n\n" * 2000
    result = await translate_text(text, "ru")
    assert "часть1\n\nчасть2" in result


async def test_translate_text_failure_returns_original(monkeypatch):
    session = _FakeSession([RuntimeError("boom")])
    monkeypatch.setattr(tr.aiohttp, "ClientSession", lambda timeout=None: session)
    assert await translate_text("hello", "ru") == "hello"
