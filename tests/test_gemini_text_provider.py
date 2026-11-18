import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import llm_provider as llm


def test_unavailable_pinned_model_uses_configured_fallback(monkeypatch):
    monkeypatch.setattr(llm, "get_gemini_api_key", lambda: "test-secret")
    monkeypatch.setattr(llm, "get_gemini_models", lambda: ["missing", "working"])
    monkeypatch.setattr(llm, "_disabled_gemini_models", set())
    monkeypatch.setattr(llm, "_log_cost", lambda *args: None)
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        assert "key=" not in url
        assert kwargs["headers"]["x-goog-api-key"] == "test-secret"
        response = requests.Response()
        response.url = url
        response.status_code = 404 if "/missing:" in url else 200
        response._content = b'{"candidates":[{"content":{"parts":[{"text":"OK"}]}}]}'
        return response

    monkeypatch.setattr(llm.requests, "post", post)
    assert llm._generate_text_gemini("test", model="missing") == "OK"
    assert len(calls) == 2
    assert "/working:" in calls[1]


def test_gemini_errors_redact_credentials(monkeypatch, capsys):
    monkeypatch.setattr(llm, "get_gemini_api_key", lambda: "test-secret")
    monkeypatch.setattr(llm, "_disabled_gemini_models", set())

    def post(*args, **kwargs):
        raise requests.ConnectionError("failed with test-secret")

    monkeypatch.setattr(llm.requests, "post", post)
    with pytest.raises(RuntimeError) as error:
        llm._generate_text_gemini("test", model="working")
    assert "test-secret" not in str(error.value)
    assert "test-secret" not in capsys.readouterr().out
