"""Tests for llm.py — config resolution, HTTP/CLI retry behaviour, and provider routing.

No test touches the network, the claude CLI, time.sleep, or the real config.yaml
(see conftest.py), so the whole file runs in well under a second.
"""

import io
import json
import subprocess
from urllib.error import HTTPError

import pytest

import llm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, payload=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else b"not json"
    return HTTPError("https://example.test", code, "error", {}, io.BytesIO(body))


def fake_urlopen(monkeypatch, *outcomes):
    """Make urlopen yield each outcome in turn: a dict is returned as JSON, an exception is raised."""
    requests = []
    queue = list(outcomes)

    def _urlopen(req, timeout):
        requests.append(req)
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeResponse(outcome)

    monkeypatch.setattr(llm, "urlopen", _urlopen)
    return requests


def fake_run(monkeypatch, *outcomes):
    """Make subprocess.run yield each outcome in turn: a CompletedProcess or an exception."""
    calls = []
    queue = list(outcomes)

    def _run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(llm.subprocess, "run", _run)
    return calls


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

class TestProviderKey:
    def test_defaults_to_anthropic_without_config(self):
        assert llm.get_provider_key() == "anthropic"

    def test_reads_config(self, write_config):
        write_config("provider: openai\n")
        assert llm.get_provider_key() == "openai"

    def test_env_overrides_config(self, write_config, monkeypatch):
        write_config("provider: openai\n")
        monkeypatch.setenv("ALLUVIUM_PROVIDER", "google")
        assert llm.get_provider_key() == "google"

    def test_empty_config_file(self, write_config):
        write_config("")
        assert llm.get_provider_key() == "anthropic"


class TestModel:
    def test_env_wins(self, write_config, monkeypatch):
        write_config("model: gpt-4o\n")
        monkeypatch.setenv("ALLUVIUM_MODEL", "custom-model")
        assert llm.get_model("openai") == "custom-model"

    def test_config_model_from_own_catalog(self, write_config):
        write_config("model: claude-haiku-4-5\n")
        assert llm.get_model("anthropic") == "claude-haiku-4-5"

    def test_config_model_from_other_provider_falls_back_to_default(self, write_config):
        # provider overridden to anthropic while config still names a claude-code alias
        write_config("model: sonnet\n")
        assert llm.get_model("anthropic") == llm.PROVIDERS["anthropic"]["default_model"]

    def test_unknown_model_is_passed_through(self, write_config):
        write_config("model: claude-some-future-model\n")
        assert llm.get_model("anthropic") == "claude-some-future-model"

    def test_default_without_config(self):
        assert llm.get_model("google") == llm.PROVIDERS["google"]["default_model"]

    def test_uses_active_provider_when_none_given(self, write_config):
        write_config("provider: mistral\n")
        assert llm.get_model() == llm.PROVIDERS["mistral"]["default_model"]


class TestApiKey:
    def test_reads_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert llm.get_api_key("openai") == "sk-test"

    def test_claude_code_needs_no_key(self):
        assert llm.get_api_key("claude-code") == ""

    def test_missing_key_exits(self):
        with pytest.raises(SystemExit):
            llm.get_api_key("anthropic")

    def test_unknown_provider_exits(self):
        with pytest.raises(SystemExit):
            llm.get_api_key("nope")


class TestParaEnabled:
    def test_defaults_to_true(self):
        assert llm.is_para_enabled() is True

    def test_reads_config(self, write_config):
        write_config("para_enabled: false\n")
        assert llm.is_para_enabled() is False

    @pytest.mark.parametrize("value, expected", [
        ("1", True), ("true", True), ("YES", True),
        ("0", False), ("false", False), ("no", False),
    ])
    def test_env_overrides_config(self, write_config, monkeypatch, value, expected):
        write_config(f"para_enabled: {str(not expected).lower()}\n")
        monkeypatch.setenv("ALLUVIUM_PARA", value)
        assert llm.is_para_enabled() is expected


# ---------------------------------------------------------------------------
# HTTP retry behaviour
# ---------------------------------------------------------------------------

class TestHttpPost:
    def test_success_first_try(self, monkeypatch, sleeps):
        requests = fake_urlopen(monkeypatch, {"ok": True})
        result = llm._http_post("https://example.test", {"X-Test": "1"}, {"a": 1})
        assert result == {"ok": True}
        assert len(requests) == 1
        assert json.loads(requests[0].data) == {"a": 1}
        assert requests[0].get_header("X-test") == "1"
        assert sleeps == []

    @pytest.mark.parametrize("code", [429, 500, 502, 503, 529])
    def test_retries_transient_http_errors(self, monkeypatch, sleeps, code):
        requests = fake_urlopen(monkeypatch, http_error(code), {"ok": True})
        assert llm._http_post("https://example.test", {}, {}) == {"ok": True}
        assert len(requests) == 2
        assert sleeps == [10]

    def test_gives_up_after_three_transient_errors(self, monkeypatch, sleeps):
        fake_urlopen(monkeypatch, http_error(503), http_error(503),
                     http_error(503, {"error": {"message": "overloaded"}}))
        with pytest.raises(RuntimeError, match=r"API error \(503\): overloaded"):
            llm._http_post("https://example.test", {}, {})
        assert sleeps == [10, 20]

    def test_client_error_is_not_retried(self, monkeypatch, sleeps):
        requests = fake_urlopen(monkeypatch, http_error(400, {"error": {"message": "bad model"}}))
        with pytest.raises(RuntimeError, match=r"API error \(400\): bad model"):
            llm._http_post("https://example.test", {}, {})
        assert len(requests) == 1
        assert sleeps == []

    def test_client_error_with_non_json_body(self, monkeypatch):
        fake_urlopen(monkeypatch, http_error(401))
        with pytest.raises(RuntimeError, match=r"API error: HTTP 401"):
            llm._http_post("https://example.test", {}, {})

    def test_retries_network_errors_then_succeeds(self, monkeypatch, sleeps):
        fake_urlopen(monkeypatch, OSError("reset"), TimeoutError("slow"), {"ok": True})
        assert llm._http_post("https://example.test", {}, {}) == {"ok": True}
        assert sleeps == [10, 20]

    def test_network_errors_exhaust_retries(self, monkeypatch, sleeps):
        fake_urlopen(monkeypatch, OSError("a"), OSError("b"), OSError("c"))
        with pytest.raises(RuntimeError, match="Network error after 3 attempts: c"):
            llm._http_post("https://example.test", {}, {})
        assert sleeps == [10, 20]


# ---------------------------------------------------------------------------
# Claude Code CLI retry behaviour
# ---------------------------------------------------------------------------

class TestClaudeCode:
    def test_success(self, monkeypatch, sleeps):
        calls = fake_run(monkeypatch, completed(stdout="  hello  \n"))
        assert llm._call_claude_code("sonnet", "prompt", 100) == "hello"
        cmd, kwargs = calls[0]
        assert cmd[:2] == ["claude", "-p"]
        assert cmd[cmd.index("--model") + 1] == "sonnet"
        # tools must stay disabled: journal content should never trigger file access
        assert cmd[cmd.index("--tools") + 1] == ""
        assert kwargs["input"] == "prompt"
        assert sleeps == []

    def test_retries_nonzero_exit(self, monkeypatch, sleeps):
        fake_run(monkeypatch, completed(returncode=1, stderr="boom"), completed(stdout="ok"))
        assert llm._call_claude_code("sonnet", "p", 100) == "ok"
        assert sleeps == [10]

    def test_retries_timeout_and_empty_output(self, monkeypatch, sleeps):
        fake_run(monkeypatch,
                 subprocess.TimeoutExpired(cmd="claude", timeout=600),
                 completed(stdout="   "),
                 completed(stdout="ok"))
        assert llm._call_claude_code("sonnet", "p", 100) == "ok"
        assert sleeps == [10, 20]

    def test_missing_cli_fails_after_retries(self, monkeypatch, sleeps):
        fake_run(monkeypatch, *[FileNotFoundError("claude")] * 3)
        with pytest.raises(RuntimeError, match="failed after 3 attempts"):
            llm._call_claude_code("sonnet", "p", 100)
        assert sleeps == [10, 20]


# ---------------------------------------------------------------------------
# Provider response parsing and routing
# ---------------------------------------------------------------------------

class TestProviders:
    def test_anthropic(self, monkeypatch):
        requests = fake_urlopen(monkeypatch, {"content": [{"text": " hi "}]})
        assert llm._call_anthropic("key", "m", "p", 50) == "hi"
        req = requests[0]
        assert req.full_url == "https://api.anthropic.com/v1/messages"
        assert req.get_header("X-api-key") == "key"
        assert json.loads(req.data)["max_tokens"] == 50

    def test_openai_compatible(self, monkeypatch):
        requests = fake_urlopen(monkeypatch, {"choices": [{"message": {"content": " hi "}}]})
        assert llm._call_openai_compatible("https://api.x.ai", "key", "m", "p", 50) == "hi"
        assert requests[0].full_url == "https://api.x.ai/v1/chat/completions"
        assert requests[0].get_header("Authorization") == "Bearer key"

    def test_gemini_quotes_api_key(self, monkeypatch):
        requests = fake_urlopen(monkeypatch, {"candidates": [{"content": {"parts": [{"text": " hi "}]}}]})
        assert llm._call_gemini("a+b&c", "gemini-2.5-pro", "p", 50) == "hi"
        assert requests[0].full_url.endswith("gemini-2.5-pro:generateContent?key=a%2Bb%26c")

    @pytest.mark.parametrize("provider, target", [
        ("anthropic", "_call_anthropic"),
        ("google", "_call_gemini"),
        ("openai", "_call_openai_compatible"),
        ("mistral", "_call_openai_compatible"),
        ("deepseek", "_call_openai_compatible"),
        ("grok", "_call_openai_compatible"),
        ("claude-code", "_call_claude_code"),
    ])
    def test_call_llm_routes_to_provider(self, monkeypatch, provider, target):
        env_key = llm.PROVIDERS[provider]["env_key"]
        if env_key:
            monkeypatch.setenv(env_key, "key")
        seen = []
        monkeypatch.setattr(llm, target, lambda *args: seen.append(args) or "reply")
        assert llm.call_llm("prompt", provider_key=provider) == "reply"
        assert len(seen) == 1
        if target == "_call_openai_compatible":
            assert seen[0][0] == llm.PROVIDERS[provider]["base_url"]


# ---------------------------------------------------------------------------
# JSON handling
# ---------------------------------------------------------------------------

class TestStripCodeFences:
    @pytest.mark.parametrize("text, expected", [
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('```\n[1]\n```', "[1]"),
        ('{"a": 1}', '{"a": 1}'),
    ])
    def test_strip(self, text, expected):
        assert llm.strip_code_fences(text) == expected


class TestCallLlmJson:
    def replies(self, monkeypatch, *texts):
        queue = list(texts)
        monkeypatch.setattr(llm, "call_llm", lambda prompt, max_tokens: queue.pop(0))
        return queue

    def test_parses_fenced_json(self, monkeypatch):
        self.replies(monkeypatch, '```json\n{"notes": []}\n```')
        assert llm.call_llm_json("p") == {"notes": []}

    def test_re_asks_after_malformed_json(self, monkeypatch):
        remaining = self.replies(monkeypatch, "{not json", '{"ok": true}')
        assert llm.call_llm_json("p") == {"ok": True}
        assert remaining == []

    def test_gives_up_after_parse_retries(self, monkeypatch):
        self.replies(monkeypatch, "x", "y", "z")
        with pytest.raises(RuntimeError, match="malformed JSON after 3 attempts"):
            llm.call_llm_json("p", parse_retries=2)
