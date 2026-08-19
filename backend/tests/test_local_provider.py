"""Tests for the offline local LLM provider (OpenAI-compatible, no cloud).

The local provider reuses the OpenAI-compatible client pointed at a local server
(Ollama / llama.cpp) with no API key required. These tests exercise provider
selection and the request shape without any network by injecting a fake HTTP
client.
"""

import asyncio

import pytest

from llm import (
    DEFAULT_LOCAL_BASE_URL,
    DEFAULT_LOCAL_MODEL,
    GeminiUnavailableError,
    OpenAICompatibleClient,
    build_client_for_provider,
)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


class _FakeHTTP:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers or {}, "json": json})
        return _FakeResponse(self.content)


def test_local_provider_is_openai_compatible_without_auth():
    client = build_client_for_provider("local")
    assert isinstance(client, OpenAICompatibleClient)
    assert client._base_url == DEFAULT_LOCAL_BASE_URL.rstrip("/")
    assert client._model == DEFAULT_LOCAL_MODEL
    assert client._require_api_key is False
    assert client._path == "/chat/completions"


def test_local_provider_env_overrides(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:8080/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "gemma2:2b")
    client = build_client_for_provider("local")
    assert client._base_url == "http://localhost:8080/v1"
    assert client._model == "gemma2:2b"


def test_factchat_provider_still_requires_key_and_trailing_slash():
    client = build_client_for_provider("factchat")
    assert client._require_api_key is True
    assert client._path == "/chat/completions/"


def test_local_generate_sends_no_auth_and_returns_content():
    client = build_client_for_provider("local")
    fake = _FakeHTTP('{"text": "hi", "emotion": "happy", "gesture": "nod"}')
    client._http = fake  # inject fake transport (bypasses network)
    raw = asyncio.run(client.generate("hello"))
    assert raw == '{"text": "hi", "emotion": "happy", "gesture": "nod"}'
    call = fake.calls[0]
    assert call["url"] == "http://localhost:11434/v1/chat/completions"
    assert "Authorization" not in call["headers"]
    assert call["json"]["model"] == DEFAULT_LOCAL_MODEL


def test_factchat_generate_without_key_raises(monkeypatch):
    monkeypatch.delenv("FACTCHAT_API_KEY", raising=False)
    client = build_client_for_provider("factchat")
    with pytest.raises(GeminiUnavailableError):
        asyncio.run(client.generate("hello"))
