"""Tests for JARVIS cortex — identity, system prompt, LLM tier order."""
import os
import sys
from pathlib import Path

import pytest

_JARVIS_HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_JARVIS_HOME / "kernel"))

import cortex  # noqa: E402


def test_agent_id():
    assert cortex.AGENT_ID == "JARVIS"


def test_system_prompt_identity():
    prompt = cortex._build_system_prompt()
    assert "JARVIS" in prompt
    assert "architect" in prompt.lower() or "arquitecto" in prompt.lower() or "Architect" in prompt
    assert "NEXUS" in prompt
    assert "ALICE" in prompt
    assert "ADA" in prompt
    assert "DUM" in prompt
    assert "William" in prompt


def test_system_prompt_ocean():
    prompt = cortex._build_system_prompt()
    assert "C=1.0" in prompt
    assert "O=0.84" in prompt
    assert "N=0.115" in prompt


def test_system_prompt_golden_rules():
    prompt = cortex._build_system_prompt()
    assert "soul_native_first" in prompt or "native" in prompt.lower()
    assert "no_external_references" in prompt or "external" in prompt.lower()


def test_temperature_default():
    os.environ.pop("JARVIS_TEMPERATURE", None)
    assert cortex._temperature() == 0.5


def test_temperature_env_override():
    os.environ["JARVIS_TEMPERATURE"] = "0.7"
    assert cortex._temperature() == 0.7
    os.environ.pop("JARVIS_TEMPERATURE")


def test_temperature_invalid_falls_back():
    os.environ["JARVIS_TEMPERATURE"] = "not_a_float"
    assert cortex._temperature() == 0.5
    os.environ.pop("JARVIS_TEMPERATURE")


def test_history_starts_empty():
    cortex.reset_history()
    assert cortex._history == []


def test_history_limit_constant():
    assert cortex._HISTORY_LIMIT == 10


def test_webchat_url_default():
    os.environ.pop("WEBCHAT_URL", None)
    import importlib
    importlib.reload(cortex)
    assert cortex.WEBCHAT_URL == "http://localhost:8765/api/agents/send"
