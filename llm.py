#!/usr/bin/env python3
"""
Alluvium — LLM Provider Abstraction
Supports multiple AI providers: Anthropic, OpenAI, Google Gemini, Mistral, DeepSeek, Grok (xAI).
Provider and model are configured in config.yaml or via environment variables.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import yaml

CONFIG_PATH = Path(__file__).parent / "config.yaml"

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDERS = {
    "anthropic": {
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "env_key": "ANTHROPIC_API_KEY",
        "models": [
            {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6 (recommended)"},
            {"id": "claude-opus-4-8", "label": "Claude Opus 4.8 (slower, more thorough)"},
            {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5 (fast, cheaper)"},
        ],
        "default_model": "claude-sonnet-4-6",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com",
        "env_key": "OPENAI_API_KEY",
        "models": [
            {"id": "gpt-4o", "label": "GPT-4o (recommended)"},
            {"id": "gpt-4.1", "label": "GPT-4.1"},
            {"id": "o4-mini", "label": "o4-mini (fast, cheaper)"},
        ],
        "default_model": "gpt-4o",
    },
    "google": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com",
        "env_key": "GOOGLE_API_KEY",
        "models": [
            {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro (recommended)"},
            {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash (fast, cheaper)"},
        ],
        "default_model": "gemini-2.5-pro",
    },
    "mistral": {
        "name": "Mistral",
        "base_url": "https://api.mistral.ai",
        "env_key": "MISTRAL_API_KEY",
        "models": [
            {"id": "mistral-large-latest", "label": "Mistral Large (recommended)"},
            {"id": "mistral-small-latest", "label": "Mistral Small (fast, cheaper)"},
        ],
        "default_model": "mistral-large-latest",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
        "models": [
            {"id": "deepseek-chat", "label": "DeepSeek Chat (recommended)"},
            {"id": "deepseek-reasoner", "label": "DeepSeek Reasoner"},
        ],
        "default_model": "deepseek-chat",
    },
    "grok": {
        "name": "Grok (xAI)",
        "base_url": "https://api.x.ai",
        "env_key": "XAI_API_KEY",
        "models": [
            {"id": "grok-3", "label": "Grok 3 (recommended)"},
            {"id": "grok-3-mini", "label": "Grok 3 Mini (fast, cheaper)"},
        ],
        "default_model": "grok-3",
    },
    "claude-code": {
        "name": "Claude Code CLI (subscription)",
        "env_key": None,
        "models": [
            {"id": "sonnet", "label": "Sonnet (default)"},
            {"id": "opus", "label": "Opus (slower, more thorough)"},
            {"id": "haiku", "label": "Haiku (fast)"},
        ],
        "default_model": "sonnet",
    },
}


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def get_provider_key() -> str:
    """Get the active provider key from env or config."""
    return os.environ.get("ALLUVIUM_PROVIDER") or _load_config().get("provider", "anthropic")


def get_model(provider_key: str | None = None) -> str:
    """Get the active model from env or config.

    A config model that belongs to a *different* provider's catalog is ignored
    (e.g. provider overridden to "anthropic" while config still says "sonnet"),
    falling back to the active provider's default. Unknown model IDs are passed
    through — they may be newer than the catalog.
    """
    env_model = os.environ.get("ALLUVIUM_MODEL")
    if env_model:
        return env_model
    pk = provider_key or get_provider_key()
    config = _load_config()
    config_model = config.get("model")
    if config_model:
        own_ids = {m["id"] for m in PROVIDERS[pk]["models"]}
        other_ids = {
            m["id"]
            for key, p in PROVIDERS.items()
            if key != pk
            for m in p["models"]
        }
        if config_model in own_ids or config_model not in other_ids:
            return config_model
        print(f"  [config] model '{config_model}' belongs to another provider — using {PROVIDERS[pk]['default_model']}")
    return PROVIDERS[pk]["default_model"]


def get_api_key(provider_key: str) -> str:
    """Get API key for a provider from environment."""
    if provider_key not in PROVIDERS:
        print(f"Error: Unknown provider '{provider_key}'.")
        print(f"Supported: {', '.join(PROVIDERS.keys())}")
        sys.exit(1)
    provider = PROVIDERS[provider_key]
    if provider.get("env_key") is None:
        return ""  # No API key needed (e.g. claude-code uses subscription)
    key = os.environ.get(provider["env_key"], "")
    if not key:
        print(f"Error: No API key found for {provider['name']}.")
        print(f"Set the {provider['env_key']} environment variable.")
        sys.exit(1)
    return key


def is_para_enabled() -> bool:
    """Check if PARA organization is enabled."""
    env = os.environ.get("ALLUVIUM_PARA")
    if env is not None:
        return env.lower() in ("1", "true", "yes")
    return _load_config().get("para_enabled", True)


# ---------------------------------------------------------------------------
# HTTP helper (stdlib only — no external dependencies)
# ---------------------------------------------------------------------------

def _http_post(url: str, headers: dict, body: dict, timeout: int = 120) -> dict:
    """POST JSON and return parsed response. Retries on transient errors."""
    data = json.dumps(body).encode("utf-8")
    max_retries = 3
    last_error = None

    for attempt in range(1, max_retries + 1):
        req = Request(url, data=data, method="POST")
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code in (429, 500, 502, 503, 529) and attempt < max_retries:
                wait = 10 * attempt
                print(f"  [retry] API returned {e.code}, attempt {attempt}/{max_retries}. Waiting {wait}s...")
                time.sleep(wait)
                last_error = e
                continue
            try:
                err_body = json.loads(e.read().decode("utf-8"))
                err_msg = err_body.get("error", {})
                if isinstance(err_msg, dict):
                    err_msg = err_msg.get("message", str(err_body))
                raise RuntimeError(f"API error ({e.code}): {err_msg}") from e
            except RuntimeError:
                raise
            except Exception:
                raise RuntimeError(f"API error: HTTP {e.code}") from e
        except OSError as e:
            last_error = e
            if attempt < max_retries:
                wait = 10 * attempt
                print(f"  [retry] Network error, attempt {attempt}/{max_retries}: {e}. Waiting {wait}s...")
                time.sleep(wait)
                continue
            raise RuntimeError(f"Network error after {max_retries} attempts: {e}") from e

    raise RuntimeError(f"HTTP request failed after {max_retries} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Provider-specific callers
# ---------------------------------------------------------------------------

def _call_anthropic(api_key: str, model: str, prompt: str, max_tokens: int) -> str:
    data = _http_post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        body={
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    return data["content"][0]["text"].strip()


def _call_openai_compatible(base_url: str, api_key: str, model: str, prompt: str, max_tokens: int) -> str:
    data = _http_post(
        f"{base_url}/v1/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        body={
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    return data["choices"][0]["message"]["content"].strip()


def _call_claude_code(model: str, prompt: str, max_tokens: int) -> str:
    """Call Claude via the Claude Code CLI (uses Mac subscription).

    Retries up to 3 times with exponential backoff on transient failures.
    """
    # --tools "" disables all tools: this is a pure text-generation call, and the
    # prompt embeds journal content that should never be able to trigger file access.
    cmd = ["claude", "-p", "--output-format", "text", "--model", model, "--max-turns", "1", "--tools", ""]
    max_retries = 3
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode != 0:
                err = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
                raise RuntimeError(f"Claude Code CLI error: {err}")
            if not result.stdout.strip():
                raise RuntimeError("Claude Code CLI returned empty output")
            return result.stdout.strip()
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
            last_error = e
            if attempt < max_retries:
                wait = 10 * attempt  # 10s, 20s
                print(f"  [retry] Claude Code CLI attempt {attempt}/{max_retries} failed: {e}")
                print(f"  [retry] Waiting {wait}s before retry...")
                time.sleep(wait)

    raise RuntimeError(f"Claude Code CLI failed after {max_retries} attempts: {last_error}")


def _call_gemini(api_key: str, model: str, prompt: str, max_tokens: int) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={quote(api_key)}"
    data = _http_post(
        url,
        headers={"Content-Type": "application/json"},
        body={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        },
    )
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def strip_code_fences(text: str) -> str:
    """Remove a wrapping markdown code fence, if present."""
    import re
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text


def call_llm_json(prompt: str, max_tokens: int = 8192, parse_retries: int = 2):
    """Call the LLM and parse the response as JSON.

    Models occasionally return malformed JSON; rather than crashing the whole
    pipeline, re-ask up to `parse_retries` times before giving up.
    """
    last_error = None
    for attempt in range(1, parse_retries + 2):
        response_text = strip_code_fences(call_llm(prompt, max_tokens=max_tokens))
        try:
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            last_error = e
            if attempt <= parse_retries:
                print(f"  [retry] LLM returned malformed JSON ({e}). Re-asking ({attempt}/{parse_retries})...")
    raise RuntimeError(f"LLM returned malformed JSON after {parse_retries + 1} attempts: {last_error}")


def call_llm(prompt: str, max_tokens: int = 8192, provider_key: str | None = None, model: str | None = None) -> str:
    """Call the configured LLM provider and return the response text.

    Provider and model default to config.yaml / environment settings.
    Can be overridden per-call for testing or special workflows.
    """
    if provider_key is None:
        provider_key = get_provider_key()
    if model is None:
        model = get_model(provider_key)

    api_key = get_api_key(provider_key)
    provider = PROVIDERS[provider_key]

    if provider_key == "claude-code":
        return _call_claude_code(model, prompt, max_tokens)
    elif provider_key == "anthropic":
        return _call_anthropic(api_key, model, prompt, max_tokens)
    elif provider_key == "google":
        return _call_gemini(api_key, model, prompt, max_tokens)
    else:
        # OpenAI-compatible: openai, mistral, deepseek, grok
        return _call_openai_compatible(provider["base_url"], api_key, model, prompt, max_tokens)
