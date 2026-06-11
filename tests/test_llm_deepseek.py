"""Tests for the DeepSeek LLM provider (bot/llm.py)."""

import pytest

from bot import llm


class FakeResponse:
    def __init__(self, payload, status=200, text=""):
        self._payload = payload
        self.status_code = status
        self.text = text or str(payload)

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def reset_provider_state(monkeypatch):
    """Each test starts with no cached provider and no real keys."""
    monkeypatch.setattr(llm, "_active_provider", None)
    monkeypatch.setattr(llm, "_active_model", None)
    monkeypatch.setattr(llm, "_probe_done", False)
    monkeypatch.setattr(llm, "_last_call_ts", 0.0)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(llm, "_get_anthropic_auth", lambda: ("", "bearer"))
    monkeypatch.setattr(llm.config, "DEEPSEEK_KEY_FILE", "/nonexistent/.deepseek-key")


def _ok_completion(text="BUY: catalyst"):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class TestKeyLookup:
    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-test")
        assert llm._get_deepseek_key() == "sk-ds-test"

    def test_key_file(self, monkeypatch, tmp_path):
        kf = tmp_path / ".deepseek-key"
        kf.write_text("sk-ds-file\n")
        monkeypatch.setattr(llm.config, "DEEPSEEK_KEY_FILE", str(kf))
        assert llm._get_deepseek_key() == "sk-ds-file"

    def test_missing(self):
        assert llm._get_deepseek_key() == ""


class TestProbeAndCall:
    def test_probe_returns_first_working_model(self, monkeypatch):
        monkeypatch.setattr(llm.requests, "post",
                            lambda *a, **k: FakeResponse(_ok_completion("hi")))
        assert llm._probe_deepseek("k") == "deepseek-v4-flash"

    def test_probe_falls_back_to_legacy_alias(self, monkeypatch):
        def fake_post(url, json=None, headers=None, timeout=None):
            if json["model"].startswith("deepseek-v4"):
                return FakeResponse({}, status=404)
            return FakeResponse(_ok_completion("hi"))

        monkeypatch.setattr(llm.requests, "post", fake_post)
        assert llm._probe_deepseek("k") == "deepseek-chat"

    def test_probe_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(llm.requests, "post",
                            lambda *a, **k: FakeResponse({}, status=401))
        assert llm._probe_deepseek("k") is None

    def test_call_parses_content(self, monkeypatch):
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["body"] = json
            captured["headers"] = headers
            return FakeResponse(_ok_completion("SKIP: nothing live"))

        monkeypatch.setattr(llm.requests, "post", fake_post)
        out = llm._call_deepseek("prompt", "system", 0.2, 100, "k", "deepseek-chat")
        assert out == "SKIP: nothing live"
        assert captured["url"] == llm.DEEPSEEK_API_URL
        assert captured["headers"]["Authorization"] == "Bearer k"
        assert captured["body"]["messages"][0] == {"role": "system", "content": "system"}
        assert captured["body"]["messages"][1] == {"role": "user", "content": "prompt"}

    def test_call_auth_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(llm.requests, "post",
                            lambda *a, **k: FakeResponse({}, status=401))
        assert llm._call_deepseek("p", "", 0.2, 100, "k", "deepseek-chat") is None

    def test_call_empty_choices_returns_none(self, monkeypatch):
        monkeypatch.setattr(llm.requests, "post",
                            lambda *a, **k: FakeResponse({"choices": []}))
        assert llm._call_deepseek("p", "", 0.2, 100, "k", "deepseek-chat") is None


class TestProviderSelection:
    def test_deepseek_preferred_when_key_present(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-test")
        monkeypatch.setattr(llm, "_probe_deepseek", lambda key: "deepseek-chat")
        assert llm._select_provider() is True
        assert llm._active_provider == "deepseek"
        assert llm._active_model == "deepseek-chat"

    def test_falls_through_when_deepseek_probe_fails(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-test")
        monkeypatch.setenv("GEMINI_API_KEY", "g-key")
        monkeypatch.setattr(llm, "_probe_deepseek", lambda key: None)
        monkeypatch.setattr(llm, "_probe_gemini", lambda key: "gemini-2.5-flash")
        assert llm._select_provider() is True
        assert llm._active_provider == "gemini"

    def test_no_providers(self):
        assert llm._select_provider() is False
        assert llm._active_provider is None

    def test_call_routes_to_deepseek(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-test")
        monkeypatch.setattr(llm, "_probe_deepseek", lambda key: "deepseek-chat")
        monkeypatch.setattr(
            llm, "_call_deepseek",
            lambda prompt, system, temp, mt, key, model: f"via-deepseek:{model}")
        out = llm.call("hello")
        assert out == "via-deepseek:deepseek-chat"
