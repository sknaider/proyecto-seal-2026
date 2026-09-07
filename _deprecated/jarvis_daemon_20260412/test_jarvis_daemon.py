#!/usr/bin/env python3
"""
Tests for JARVIS Daemon — pensamiento autónomo entre sesiones
==============================================================
Tests: prompt building, emotional classification, thought parsing,
DB integration, Ollama mock, full cycle.

Run: python3 test_jarvis_daemon.py
"""

import asyncio
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

import jarvis_daemon as jd


class TestPromptBuilding(unittest.TestCase):
    """Test system and think prompt construction."""

    def test_system_prompt_contains_identity(self):
        identity = {
            "personality": "Soy JARVIS, el arquitecto.",
            "ocean": {"O": 0.8, "C": 0.9, "E": 0.4, "A": 0.7, "N": 0.1},
        }
        prompt = jd.build_system_prompt(identity)
        self.assertIn("JARVIS", prompt)
        self.assertIn("architect", prompt.lower() or prompt)
        self.assertIn("O=0.80", prompt)
        self.assertIn("C=0.90", prompt)
        self.assertIn("Spanish", prompt)

    def test_system_prompt_default_ocean(self):
        identity = {"personality": "", "ocean": {}}
        prompt = jd.build_system_prompt(identity)
        # Should use DEFAULT_OCEAN fallbacks via .get()
        self.assertIn("O=", prompt)

    def test_think_prompt_with_memories(self):
        memories = [
            {"category": "fact", "importance": 7, "content": "Test memory content"},
            {"category": "decision", "importance": 9, "content": "Important decision"},
        ]
        prompt = jd.build_think_prompt(memories, [], None)
        self.assertIn("Test memory content", prompt)
        self.assertIn("Important decision", prompt)
        self.assertIn("imp=7", prompt)
        self.assertIn("imp=9", prompt)

    def test_think_prompt_with_team_messages(self):
        messages = ["[ADA→JARVIS] Implementé el fix", "[JARVIS→ADA] Bien hecho"]
        prompt = jd.build_think_prompt([], messages, None)
        self.assertIn("ADA→JARVIS", prompt)
        self.assertIn("Team messages", prompt)

    def test_think_prompt_with_last_thought(self):
        last = {
            "thought": "Estoy pensando en la fase 2",
            "emotional_state": "strategic",
            "created_at": "2026-04-08T20:00:00+00:00",
        }
        prompt = jd.build_think_prompt([], [], last)
        self.assertIn("Estoy pensando en la fase 2", prompt)
        self.assertIn("strategic", prompt)

    def test_think_prompt_empty_context(self):
        prompt = jd.build_think_prompt([], [], None)
        self.assertIn("Instruction", prompt)
        self.assertIn("Current time", prompt)

    def test_think_prompt_truncates_long_content(self):
        memories = [{"category": "fact", "importance": 5, "content": "x" * 300}]
        prompt = jd.build_think_prompt(memories, [], None)
        # Content should be truncated to 150 chars
        self.assertLessEqual(
            len([line for line in prompt.split("\n") if "xxx" in line][0]),
            200,  # generous limit for the formatted line
        )


class TestEmotionalState(unittest.TestCase):
    """Test emotional state classification."""

    def test_concerned(self):
        self.assertEqual(jd.parse_emotional_state("Estoy preocupado por ADA"), "concerned")
        self.assertEqual(jd.parse_emotional_state("[URGENTE] algo está mal"), "concerned")

    def test_proud(self):
        self.assertEqual(jd.parse_emotional_state("Me siento orgulloso del equipo"), "proud")
        self.assertEqual(jd.parse_emotional_state("Excelente progreso hoy"), "proud")

    def test_curious(self):
        self.assertEqual(jd.parse_emotional_state("Me pregunto si podemos mejorar"), "curious")
        self.assertEqual(jd.parse_emotional_state("Investigar el nuevo approach"), "curious")

    def test_strategic(self):
        self.assertEqual(jd.parse_emotional_state("El plan para mañana es claro"), "strategic")
        self.assertEqual(jd.parse_emotional_state("La siguiente estrategia debería ser"), "strategic")

    def test_calm(self):
        self.assertEqual(jd.parse_emotional_state("Todo bien, sistema estable"), "calm")

    def test_default_reflective(self):
        self.assertEqual(jd.parse_emotional_state("Observando el flujo de datos"), "reflective")
        self.assertEqual(jd.parse_emotional_state(""), "reflective")


class TestOllamaCall(unittest.TestCase):
    """Test Ollama HTTP call handling."""

    @patch("jarvis_daemon.urllib.request.urlopen")
    def test_successful_call(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"response": "Pensamiento generado"}).encode()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = jd.call_ollama("test prompt", system="test system")
        self.assertEqual(result, "Pensamiento generado")

    @patch("jarvis_daemon.urllib.request.urlopen")
    def test_empty_response(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"response": ""}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = jd.call_ollama("test")
        self.assertEqual(result, "")

    @patch("jarvis_daemon.urllib.request.urlopen")
    def test_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        result = jd.call_ollama("test")
        self.assertIsNone(result)

    @patch("jarvis_daemon.urllib.request.urlopen")
    def test_timeout(self, mock_urlopen):
        import socket
        mock_urlopen.side_effect = socket.timeout("timed out")
        result = jd.call_ollama("test")
        self.assertIsNone(result)


class TestWebChat(unittest.TestCase):
    """Test web chat posting."""

    @patch("jarvis_daemon.urllib.request.urlopen")
    def test_successful_post(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = jd.send_webchat("test message")
        self.assertTrue(result)

    @patch("jarvis_daemon.urllib.request.urlopen")
    def test_failed_post(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("refused")
        result = jd.send_webchat("test")
        self.assertFalse(result)


class TestUrgentGuard(unittest.TestCase):
    """Rate-limit + dedup gate for urgent webchat publishes."""

    def setUp(self):
        import time
        jd._urgent_history = []
        self._time = time

    def test_first_urgent_publishes(self):
        allow, reason = jd._should_publish_urgent(
            "[URGENTE] ADA caída, reiniciar ahora"
        )
        self.assertTrue(allow, f"First urgent must pass, got reason={reason}")
        self.assertEqual(len(jd._urgent_history), 1)

    def test_rate_limit_blocks_immediate_second(self):
        jd._should_publish_urgent("[URGENTE] Primera alerta totalmente única aquí")
        allow, reason = jd._should_publish_urgent(
            "[URGENTE] Segunda alerta completamente distinta de la primera pelota"
        )
        self.assertFalse(allow)
        self.assertIn("rate_limit", reason)

    def test_dedup_catches_rephrasing(self):
        # Seed history with an old-enough entry to bypass rate-limit.
        # Use 3-tuple (ts, token_set, embedding=None) — None triggers Jaccard fallback.
        # The two phrases share >50% tokens ("henry", "proteger"/"protección",
        # "sleepgate", "crítico") so Jaccard fallback blocks the duplicate.
        old_ts = self._time.time() - (jd.URGENT_MIN_INTERVAL + 200)
        jd._urgent_history = [
            (
                old_ts,
                jd._tokenize_urgent(
                    "[URGENTE] Debo proteger a Henry y asegurar SleepGate crítico ahora"
                ),
                None,  # embedding=None → Jaccard fallback path
            )
        ]
        allow, reason = jd._should_publish_urgent(
            "[URGENTE] Henry necesita protección y SleepGate es crítico ahora"
        )
        self.assertFalse(allow)
        self.assertIn("dedup", reason)

    def test_different_topic_passes_after_rate_window(self):
        old_ts = self._time.time() - (jd.URGENT_MIN_INTERVAL + 200)
        jd._urgent_history = [
            (
                old_ts,
                jd._tokenize_urgent("[URGENTE] Temperatura GPU subiendo demasiado alto"),
                None,  # embedding=None → Jaccard fallback path
            )
        ]
        allow, reason = jd._should_publish_urgent(
            "[URGENTE] ADA caída reinicio necesario inmediato"
        )
        self.assertTrue(allow, f"blocked with reason={reason}")

    def test_tokenize_strips_stopwords_and_tag(self):
        tokens = jd._tokenize_urgent("[URGENTE] Debo proteger a Henry")
        self.assertNotIn("debo", tokens)
        self.assertNotIn("urgente", tokens)
        self.assertIn("proteger", tokens)
        self.assertIn("henry", tokens)

    def test_empty_thought_rejected(self):
        allow, reason = jd._should_publish_urgent("[URGENTE]")
        self.assertFalse(allow)
        self.assertEqual(reason, "empty_tokens")

    def test_history_bounded(self):
        # Force history into many distinct allowed entries
        jd.URGENT_MIN_INTERVAL = 0  # temporarily disable rate-limit
        try:
            for i in range(jd.URGENT_HASH_HISTORY + 5):
                jd._should_publish_urgent(
                    f"[URGENTE] Alerta única identificador {i} tema diferente palabra{i}"
                )
            self.assertLessEqual(len(jd._urgent_history), jd.URGENT_HASH_HISTORY)
        finally:
            jd.URGENT_MIN_INTERVAL = int(
                os.getenv("JARVIS_URGENT_MIN_INTERVAL", "1800")
            )


class TestHeartbeat(unittest.TestCase):
    """Test heartbeat file writing."""

    def test_write_heartbeat(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)

        original = jd.HEARTBEAT_PATH
        jd.HEARTBEAT_PATH = tmp_path
        try:
            jd.write_heartbeat("testing", {"cycle": 1})
            data = json.loads(tmp_path.read_text())
            self.assertEqual(data["agent"], "JARVIS-daemon")
            self.assertTrue(data["alive"])
            self.assertEqual(data["status"], "testing")
            self.assertEqual(data["cycle"], 1)
            self.assertIn("pid", data)
            self.assertIn("timestamp", data)
        finally:
            jd.HEARTBEAT_PATH = original
            tmp_path.unlink(missing_ok=True)


class TestDatabaseIntegration(unittest.TestCase):
    """Test database operations against real PostgreSQL."""

    def setUp(self):
        """Check if PG is available."""
        try:
            import asyncpg
            self._has_pg = True
        except ImportError:
            self._has_pg = False

    def _run_async(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_fetch_identity(self):
        if not self._has_pg:
            self.skipTest("asyncpg not installed")
        result = self._run_async(jd.fetch_identity())
        self.assertIn("personality", result)
        self.assertIn("ocean", result)
        self.assertIsInstance(result["ocean"], dict)

    def test_fetch_recent_memories(self):
        if not self._has_pg:
            self.skipTest("asyncpg not installed")
        result = self._run_async(jd.fetch_recent_memories(5))
        self.assertIsInstance(result, list)
        if result:
            self.assertIn("content", result[0])
            self.assertIn("category", result[0])

    def test_fetch_last_thought(self):
        if not self._has_pg:
            self.skipTest("asyncpg not installed")
        result = self._run_async(jd.fetch_last_thought())
        # May be None if no thoughts exist, that's OK
        if result:
            self.assertIn("thought", result)

    def test_write_and_fetch_thought(self):
        if not self._has_pg:
            self.skipTest("asyncpg not installed")
        # Write a test thought
        test_thought = f"[daemon-test] Test thought at {datetime.now(timezone.utc).isoformat()}"
        thought_id = self._run_async(jd.write_thought(test_thought, "testing"))
        self.assertIsNotNone(thought_id)
        self.assertIsInstance(thought_id, int)

        # Verify it's the latest
        last = self._run_async(jd.fetch_last_thought())
        self.assertIsNotNone(last)
        self.assertIn("[daemon-test]", last["thought"])

    def test_fetch_team_messages(self):
        result = self._run_async(jd.fetch_recent_team_messages(3))
        self.assertIsInstance(result, list)

    def tearDown(self):
        if self._has_pg:
            self._run_async(jd.close_pool())


class TestThinkCycle(unittest.TestCase):
    """Test the full think cycle with mocked Ollama."""

    def _run_async(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    @patch("jarvis_daemon.call_ollama")
    def test_full_cycle_with_mock(self, mock_ollama):
        mock_ollama.return_value = "Reflexión: necesito planificar la siguiente fase del framework."
        try:
            result = self._run_async(jd.think_cycle())
            self.assertEqual(result["status"], "ok")
            self.assertIsNotNone(result["thought"])
            self.assertIn("Reflexión", result["thought"])
            self.assertIsNotNone(result["thought_id"])
            self.assertEqual(result["emotional_state"], "strategic")  # "siguiente" keyword
        except Exception as e:
            if "asyncpg" in str(e) or "connect" in str(e).lower():
                self.skipTest(f"DB not available: {e}")
            raise

    @patch("jarvis_daemon.call_ollama")
    def test_cycle_ollama_failure(self, mock_ollama):
        mock_ollama.return_value = None
        try:
            result = self._run_async(jd.think_cycle())
            self.assertEqual(result["status"], "ollama_failed")
            self.assertIsNone(result["thought"])
        except Exception as e:
            if "asyncpg" in str(e) or "connect" in str(e).lower():
                self.skipTest(f"DB not available: {e}")
            raise

    @patch("jarvis_daemon.call_ollama")
    @patch("jarvis_daemon.send_webchat")
    def test_cycle_urgent_triggers_webchat(self, mock_webchat, mock_ollama):
        mock_ollama.return_value = "[URGENTE] ADA no responde, posible crash"
        mock_webchat.return_value = True
        try:
            result = self._run_async(jd.think_cycle())
            self.assertTrue(result["urgent"])
            mock_webchat.assert_called_once()
            self.assertIn("[URGENTE]", mock_webchat.call_args[0][0])
        except Exception as e:
            if "asyncpg" in str(e) or "connect" in str(e).lower():
                self.skipTest(f"DB not available: {e}")
            raise

    @patch("jarvis_daemon.call_ollama")
    def test_cycle_empty_thought(self, mock_ollama):
        mock_ollama.return_value = "   "
        try:
            result = self._run_async(jd.think_cycle())
            self.assertEqual(result["status"], "empty_thought")
        except Exception as e:
            if "asyncpg" in str(e) or "connect" in str(e).lower():
                self.skipTest(f"DB not available: {e}")
            raise

    def tearDown(self):
        self._run_async(jd.close_pool())


# ── Runner ──

def run_tests():
    """Run all tests and report."""
    print("=" * 60)
    print("JARVIS Daemon Test Suite")
    print("=" * 60)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestPromptBuilding,
        TestEmotionalState,
        TestOllamaCall,
        TestWebChat,
        TestUrgentGuard,
        TestHeartbeat,
        TestDatabaseIntegration,
        TestThinkCycle,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    skipped = len(result.skipped)
    passed = total - failed - skipped
    print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
    if failed == 0:
        print("✅ ALL TESTS PASSED")
    else:
        print("❌ FAILURES:")
        for test, traceback in result.failures + result.errors:
            print(f"  - {test}")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    import urllib.error
    success = run_tests()
    sys.exit(0 if success else 1)
