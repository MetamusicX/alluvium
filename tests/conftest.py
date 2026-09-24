"""Shared fixtures: keep every test offline, instant, and independent of the real config."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm  # noqa: E402

ENV_VARS = [
    "ALLUVIUM_PROVIDER",
    "ALLUVIUM_MODEL",
    "ALLUVIUM_PARA",
    *[p["env_key"] for p in llm.PROVIDERS.values() if p["env_key"]],
]


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Point llm at an empty temp config and clear provider-related env vars."""
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(llm, "CONFIG_PATH", tmp_path / "config.yaml")


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """Record retry back-off waits instead of actually sleeping."""
    calls = []
    monkeypatch.setattr(llm.time, "sleep", calls.append)
    return calls


@pytest.fixture(autouse=True)
def no_real_io(monkeypatch):
    """Fail loudly if a test forgets to mock the network or the claude CLI."""
    def blocked(*args, **kwargs):
        raise AssertionError("test attempted real network/subprocess I/O")
    monkeypatch.setattr(llm, "urlopen", blocked)
    monkeypatch.setattr(llm.subprocess, "run", blocked)


@pytest.fixture
def write_config():
    """Write YAML text to the temp config file used by llm."""
    def _write(text):
        llm.CONFIG_PATH.write_text(text)
    return _write
