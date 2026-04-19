"""Tests for optional extras — PostgresBackend, SentenceTransformerEmbedding, OllamaProvider.

These test import behavior and error handling without requiring actual services.
"""

import pytest


# =============================================================================
# PostgresBackend
# =============================================================================


class TestPostgresBackend:
    def test_import_without_asyncpg(self):
        """PostgresBackend should be importable even without asyncpg installed."""
        from soul_framework.backend.postgres import PostgresBackend
        assert PostgresBackend is not None

    def test_raises_without_asyncpg_on_init(self):
        """If asyncpg isn't installed, init should raise ImportError."""
        from soul_framework.backend import postgres
        original = postgres.asyncpg
        try:
            postgres.asyncpg = None
            with pytest.raises(ImportError, match="asyncpg"):
                postgres.PostgresBackend("postgresql://localhost/test")
        finally:
            postgres.asyncpg = original

    def test_schema_has_all_tables(self):
        """PostgreSQL schema should have the same tables as SQLite."""
        from soul_framework.backend.postgres import POSTGRES_SCHEMA_SQL
        required_tables = [
            "memories", "identity", "relationships", "rules",
            "inner_monologue", "diary", "instincts", "working_state",
        ]
        for table in required_tables:
            assert f"CREATE TABLE IF NOT EXISTS {table}" in POSTGRES_SCHEMA_SQL

    def test_schema_uses_serial(self):
        """PostgreSQL schema should use SERIAL not AUTOINCREMENT."""
        from soul_framework.backend.postgres import POSTGRES_SCHEMA_SQL
        assert "SERIAL" in POSTGRES_SCHEMA_SQL
        assert "AUTOINCREMENT" not in POSTGRES_SCHEMA_SQL

    def test_schema_uses_timestamptz(self):
        """PostgreSQL schema should use TIMESTAMPTZ."""
        from soul_framework.backend.postgres import POSTGRES_SCHEMA_SQL
        assert "TIMESTAMPTZ" in POSTGRES_SCHEMA_SQL

    def test_schema_uses_jsonb(self):
        """PostgreSQL schema should use JSONB for structured data."""
        from soul_framework.backend.postgres import POSTGRES_SCHEMA_SQL
        assert "JSONB" in POSTGRES_SCHEMA_SQL

    def test_not_initialized_raises(self):
        """Calling methods before initialize() should raise."""
        from soul_framework.backend import postgres
        if postgres.asyncpg is None:
            pytest.skip("asyncpg not installed")
        backend = postgres.PostgresBackend("postgresql://localhost/test")
        with pytest.raises(RuntimeError, match="not initialized"):
            backend._get_pool()


# =============================================================================
# SentenceTransformerEmbedding
# =============================================================================


class TestSentenceTransformerEmbedding:
    def test_import_without_library(self):
        """Module should be importable even without sentence-transformers."""
        from soul_framework.embedding.sentence_transformer import SentenceTransformerEmbedding
        assert SentenceTransformerEmbedding is not None

    def test_raises_without_library_on_init(self):
        """If sentence-transformers isn't installed, init should raise ImportError."""
        from soul_framework.embedding import sentence_transformer as mod
        original = mod.SentenceTransformer
        try:
            mod.SentenceTransformer = None
            with pytest.raises(ImportError, match="sentence-transformers"):
                mod.SentenceTransformerEmbedding()
        finally:
            mod.SentenceTransformer = original


# =============================================================================
# OllamaProvider
# =============================================================================


class TestOllamaProvider:
    def test_import_without_httpx(self):
        """Module should be importable even without httpx."""
        from soul_framework.llm.ollama import OllamaProvider
        assert OllamaProvider is not None

    def test_raises_without_httpx_on_init(self):
        """If httpx isn't installed, init should raise ImportError."""
        from soul_framework.llm import ollama as mod
        original = mod.httpx
        try:
            mod.httpx = None
            with pytest.raises(ImportError, match="httpx"):
                mod.OllamaProvider()
        finally:
            mod.httpx = original

    def test_default_config(self):
        """OllamaProvider should have sensible defaults."""
        from soul_framework.llm import ollama as mod
        if mod.httpx is None:
            pytest.skip("httpx not installed")
        provider = mod.OllamaProvider()
        assert provider._model == "qwen2.5:7b"
        assert "11434" in provider._base_url

    def test_custom_config(self):
        """OllamaProvider should accept custom model and URL."""
        from soul_framework.llm import ollama as mod
        if mod.httpx is None:
            pytest.skip("httpx not installed")
        provider = mod.OllamaProvider(
            model="llama3:8b",
            base_url="http://10.0.0.1:11434",
            timeout=120.0,
        )
        assert provider._model == "llama3:8b"
        assert provider._base_url == "http://10.0.0.1:11434"
        assert provider._timeout == 120.0
