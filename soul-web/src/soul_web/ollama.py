"""Bounded Ollama client used by the local SOUL conversational surface."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

_EMBEDDING_MARKERS = (
    "embed",
    "minilm",
    "bge-m3",
    "bge-large",
    "bge-small",
    "gte-",
    "e5-",
)


@dataclass(frozen=True, slots=True)
class ExtractedFact:
    """A durable user fact bound to verbatim evidence from the user message."""

    fact: str
    evidence: str
    confidence: float = 1.0


class OllamaError(RuntimeError):
    """Raised when the local Ollama contract is unavailable or malformed."""


class OllamaClient:
    """Small synchronous client with bounded requests and strict extraction output."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 180.0,
        max_response_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = float(timeout)
        self._max_response_bytes = int(max_response_bytes)
        if self._timeout <= 0 or self._max_response_bytes < 1024:
            raise ValueError("invalid Ollama bounds")

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._base_url + path,
            data=data,
            method="GET" if data is None else "POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read(self._max_response_bytes + 1)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise OllamaError(f"Ollama request failed: {exc}") from exc
        if len(raw) > self._max_response_bytes:
            raise OllamaError("Ollama response exceeded configured limit")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OllamaError("Ollama returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise OllamaError("Ollama returned a non-object response")
        return value

    def list_chat_models(self) -> list[str]:
        """List usable chat models while excluding embedding-only models."""

        value = self._request("/api/tags")
        models = value.get("models")
        if not isinstance(models, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for item in models[:200]:
            name = item.get("name") if isinstance(item, dict) else None
            if not isinstance(name, str):
                continue
            name = name.strip()
            lowered = name.casefold()
            if (
                not name
                or len(name) > 256
                or any(char in name for char in ("\x00", "\r", "\n"))
                or any(marker in lowered for marker in _EMBEDDING_MARKERS)
                or name in seen
            ):
                continue
            seen.add(name)
            result.append(name)
        return result

    def chat(self, *, model: str, system: str, user: str, temperature: float = 0.4) -> str:
        value = self._request(
            "/api/chat",
            {
                "model": model,
                "stream": False,
                "options": {"temperature": float(temperature)},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        message = value.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise OllamaError("Ollama chat response did not contain text")
        return content.strip()

    def extract_facts(
        self,
        *,
        model: str,
        user_message: str,
        assistant_message: str = "",
    ) -> list[ExtractedFact]:
        """Extract durable user facts and bind each one to verbatim source evidence.

        This runs for every user turn, including questions that contain a fact.  Facts
        without an exact evidence span are rejected, preventing the chat model from
        silently promoting its own guesses into long-term memory.
        """

        system = (
            "Extrae SOLO hechos duraderos que el usuario declara sobre sí mismo, sus "
            "preferencias, decisiones, relaciones, trabajo o proyectos. Analiza todo el "
            "mensaje aunque también sea una pregunta. Devuelve JSON estricto con esta "
            "forma: {\"facts\":[{\"fact\":\"...\",\"evidence\":\"cita literal exacta "
            "del mensaje\",\"confidence\":0.0}]}. No uses conocimiento de la respuesta "
            "del asistente. La respuesta del asistente se incluye únicamente para resolver "
            "referencias, pero cada evidence debe ser una cita literal del mensaje del usuario. "
            "No inventes. Si no hay hechos, devuelve {\"facts\":[]}."
        )
        extraction_input = (
            f"MENSAJE DEL USUARIO:\n{user_message}\n\n"
            f"RESPUESTA DEL ASISTENTE (solo contexto):\n{assistant_message}"
        )
        value = self._request(
            "/api/chat",
            {
                "model": model,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": extraction_input},
                ],
            },
        )
        message = value.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise OllamaError("fact extractor returned no JSON text")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise OllamaError("fact extractor returned invalid JSON") from exc
        rows = parsed.get("facts") if isinstance(parsed, dict) else None
        if not isinstance(rows, list):
            raise OllamaError("fact extractor JSON has no facts list")

        message_folded = user_message.casefold()
        result: list[ExtractedFact] = []
        seen: set[str] = set()
        for row in rows[:10]:
            if not isinstance(row, dict):
                continue
            fact = row.get("fact")
            evidence = row.get("evidence")
            confidence = row.get("confidence", 1.0)
            if not isinstance(fact, str) or not isinstance(evidence, str):
                continue
            fact, evidence = fact.strip(), evidence.strip()
            try:
                confidence_value = max(0.0, min(1.0, float(confidence)))
            except (TypeError, ValueError):
                confidence_value = 0.0
            normalized = " ".join(fact.casefold().split())
            if (
                len(fact) < 5
                or len(fact) > 300
                or len(evidence) < 2
                or evidence.casefold() not in message_folded
                or confidence_value < 0.5
                or normalized in seen
            ):
                continue
            seen.add(normalized)
            result.append(ExtractedFact(fact, evidence, confidence_value))
        return result

    def summarize_episode(self, *, model: str, transcript: str) -> str:
        """Create a compact factual episode summary from a bounded transcript."""

        system = (
            "Resume este episodio de conversación para memoria a largo plazo. Conserva "
            "hechos, decisiones, preferencias, compromisos, correcciones y asuntos abiertos. "
            "No inventes ni agregues conocimiento externo. Escribe un párrafo breve en español."
        )
        return self.chat(
            model=model,
            system=system,
            user=transcript[:24_000],
            temperature=0.0,
        )
