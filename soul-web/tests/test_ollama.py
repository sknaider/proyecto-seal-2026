from __future__ import annotations

import json

import pytest
from soul_web.ollama import OllamaClient, OllamaError


class Response:
    def __init__(self, value):
        self.status = 200
        self._raw = json.dumps(value).encode()

    def read(self, size=-1):
        return self._raw[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_chat_models_exclude_embedding_models(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: Response(
            {"models": [{"name": "gemma3:4b"}, {"name": "bge-m3:latest"}]}
        ),
    )
    assert OllamaClient().list_chat_models() == ["gemma3:4b"]


def test_fact_extraction_requires_verbatim_evidence(monkeypatch):
    payload = {
        "message": {
            "content": json.dumps(
                {
                    "facts": [
                        {
                            "fact": "William vive en Chiclayo",
                            "evidence": "vivo en Chiclayo",
                            "confidence": 0.95,
                        },
                        {
                            "fact": "William es astronauta",
                            "evidence": "soy astronauta",
                            "confidence": 0.99,
                        },
                    ]
                }
            )
        }
    }
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: Response(payload))
    facts = OllamaClient().extract_facts(
        model="gemma3:4b", user_message="Aunque sea una pregunta, vivo en Chiclayo, ¿recuerdas?"
    )
    assert [fact.fact for fact in facts] == ["William vive en Chiclayo"]


def test_fact_extraction_fails_loud_on_invalid_contract(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: Response({"message": {"content": "no-json"}}),
    )
    with pytest.raises(OllamaError, match="invalid JSON"):
        OllamaClient().extract_facts(model="gemma3:4b", user_message="vivo en Lima")
