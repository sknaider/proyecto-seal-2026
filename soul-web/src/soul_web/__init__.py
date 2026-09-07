"""SOUL Web: local chat with durable, auditable conversational memory."""

from .ollama import ExtractedFact, OllamaClient

__all__ = ["ExtractedFact", "OllamaClient"]
__version__ = "0.1.4"
