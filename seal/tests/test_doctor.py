"""Tests for seal/doctor.py — self-diagnostic CLI.

Run:
    python3 -m pytest seal/tests/test_doctor.py -v
"""
from __future__ import annotations

import json
import os
import socket
import socketserver
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

from seal.doctor import (
    Status,
    check_claude_cli,
    check_credentials,
    check_gateway,
    check_mcp_sse,
    check_neo4j,
    check_postgres,
    check_qdrant,
    check_tailscale,
    print_table,
    run_doctor,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _OKHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a: Any) -> None:
        pass


class _ServerThread(threading.Thread):
    def __init__(self, port: int) -> None:
        super().__init__(daemon=True)
        self.server = HTTPServer(("127.0.0.1", port), _OKHandler)

    def run(self) -> None:
        self.server.serve_forever()

    def stop(self) -> None:
        self.server.shutdown()


# ── TCP helpers ───────────────────────────────────────────────────────────────


class TcpCheckTests(unittest.TestCase):
    def test_refused_port_returns_false(self) -> None:
        port = _free_port()
        from seal.doctor import _tcp
        self.assertFalse(_tcp("127.0.0.1", port, timeout=0.5))

    def test_open_port_returns_true(self) -> None:
        port = _free_port()
        srv = socketserver.TCPServer(("127.0.0.1", port), socketserver.BaseRequestHandler)
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()
        from seal.doctor import _tcp
        result = _tcp("127.0.0.1", port, timeout=1.0)
        srv.server_close()
        self.assertTrue(result)


# ── Postgres check ────────────────────────────────────────────────────────────


class PostgresCheckTests(unittest.TestCase):
    def test_fail_when_port_closed(self) -> None:
        port = _free_port()
        with patch.dict(os.environ, {"SEAL_PG_PORT": str(port)}):
            status, detail = check_postgres()
        self.assertEqual(status, "FAIL")
        self.assertIn(str(port), detail)

    def test_warn_when_tcp_ok_but_psycopg2_missing(self) -> None:
        port = _free_port()
        srv = socketserver.TCPServer(("127.0.0.1", port), socketserver.BaseRequestHandler)
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()
        import builtins
        real_import = builtins.__import__
        def _no_psycopg2(name, *args, **kwargs):
            if name == "psycopg2":
                raise ImportError("mocked")
            return real_import(name, *args, **kwargs)
        with patch.dict(os.environ, {"SEAL_PG_PORT": str(port), "SEAL_PG_HOST": "127.0.0.1"}):
            with patch("builtins.__import__", side_effect=_no_psycopg2):
                status, detail = check_postgres()
        srv.server_close()
        self.assertEqual(status, "WARN")
        self.assertIn("psycopg2", detail)


# ── Qdrant check ──────────────────────────────────────────────────────────────


class QdrantCheckTests(unittest.TestCase):
    def test_ok_when_health_returns_200(self) -> None:
        port = _free_port()
        srv_thread = _ServerThread(port)
        srv_thread.start()
        try:
            with patch.dict(os.environ, {"QDRANT_HOST": "127.0.0.1", "QDRANT_PORT": str(port)}):
                status, detail = check_qdrant()
            self.assertEqual(status, "OK")
        finally:
            srv_thread.stop()

    def test_fail_when_port_closed(self) -> None:
        port = _free_port()
        with patch.dict(os.environ, {"QDRANT_HOST": "127.0.0.1", "QDRANT_PORT": str(port)}):
            status, detail = check_qdrant()
        self.assertEqual(status, "FAIL")


# ── Neo4j check ───────────────────────────────────────────────────────────────


class Neo4jCheckTests(unittest.TestCase):
    def test_fail_when_port_closed(self) -> None:
        port = _free_port()
        with patch.dict(os.environ, {"NEO4J_PORT": str(port)}):
            with patch("seal.doctor._tcp", return_value=False):
                status, detail = check_neo4j()
        self.assertEqual(status, "FAIL")

    def test_ok_when_bolt_reachable(self) -> None:
        with patch("seal.doctor._tcp", return_value=True):
            status, detail = check_neo4j()
        self.assertEqual(status, "OK")


# ── Gateway / MCP checks ──────────────────────────────────────────────────────


class GatewayCheckTests(unittest.TestCase):
    def test_ok_when_health_200(self) -> None:
        port = _free_port()
        srv_thread = _ServerThread(port)
        srv_thread.start()
        try:
            url = f"http://127.0.0.1:{port}/health"
            with patch.dict(os.environ, {"SEAL_GATEWAY_URL": url}):
                status, detail = check_gateway()
            self.assertEqual(status, "OK")
        finally:
            srv_thread.stop()

    def test_fail_when_unreachable(self) -> None:
        port = _free_port()
        url = f"http://127.0.0.1:{port}/health"
        with patch.dict(os.environ, {"SEAL_GATEWAY_URL": url}):
            status, detail = check_gateway()
        self.assertEqual(status, "FAIL")


class McpSseCheckTests(unittest.TestCase):
    def test_ok_when_health_200(self) -> None:
        port = _free_port()
        srv_thread = _ServerThread(port)
        srv_thread.start()
        try:
            url = f"http://127.0.0.1:{port}/health"
            with patch.dict(os.environ, {"SEAL_MCP_URL": url}):
                status, detail = check_mcp_sse()
            self.assertEqual(status, "OK")
        finally:
            srv_thread.stop()

    def test_fail_when_unreachable(self) -> None:
        port = _free_port()
        url = f"http://127.0.0.1:{port}/health"
        with patch.dict(os.environ, {"SEAL_MCP_URL": url}):
            status, detail = check_mcp_sse()
        self.assertEqual(status, "FAIL")


# ── Tailscale check ───────────────────────────────────────────────────────────


class TailscaleCheckTests(unittest.TestCase):
    def test_warn_when_no_cli(self) -> None:
        with patch("shutil.which", return_value=None):
            with patch("subprocess.run", side_effect=FileNotFoundError):
                status, detail = check_tailscale()
        self.assertEqual(status, "WARN")

    def test_ok_when_running(self) -> None:
        ts_json = json.dumps({
            "BackendState": "Running",
            "TailscaleIPs": ["100.64.0.1"],
        })
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ts_json
        with patch("shutil.which", return_value="/usr/bin/tailscale"):
            with patch("subprocess.run", return_value=mock_result):
                status, detail = check_tailscale()
        self.assertEqual(status, "OK")
        self.assertIn("100.64.0.1", detail)

    def test_warn_when_not_running(self) -> None:
        ts_json = json.dumps({"BackendState": "Stopped", "TailscaleIPs": []})
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ts_json
        with patch("shutil.which", return_value="/usr/bin/tailscale"):
            with patch("subprocess.run", return_value=mock_result):
                status, detail = check_tailscale()
        self.assertEqual(status, "WARN")


# ── Claude CLI check ──────────────────────────────────────────────────────────


class ClaudeCliCheckTests(unittest.TestCase):
    def test_ok_when_in_path(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            status, detail = check_claude_cli()
        self.assertEqual(status, "OK")
        self.assertIn("claude", detail)

    def test_fail_when_not_found(self) -> None:
        with patch("shutil.which", return_value=None):
            with patch("os.path.isfile", return_value=False):
                status, detail = check_claude_cli()
        self.assertEqual(status, "FAIL")


# ── Credentials check ─────────────────────────────────────────────────────────


class CredentialsCheckTests(unittest.TestCase):
    def test_fail_when_required_missing(self) -> None:
        env = {k: "" for k in os.environ}
        env.pop("ANTHROPIC_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            status, detail = check_credentials()
        self.assertEqual(status, "FAIL")
        self.assertIn("Anthropic", detail)

    def test_ok_when_required_present(self) -> None:
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            status, detail = check_credentials()
        self.assertIn(status, ("OK", "WARN"))

    def test_ok_with_optional_also_present(self) -> None:
        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "OPENAI_API_KEY": "sk-openai-test",
        }):
            status, detail = check_credentials()
        self.assertEqual(status, "OK")
        self.assertIn("OpenAI", detail)


# ── run_doctor integration ────────────────────────────────────────────────────


class RunDoctorTests(unittest.TestCase):
    def test_returns_int(self) -> None:
        with patch("seal.doctor._run_all", return_value=[
            ("Foo", "OK", "detail"),
            ("Bar", "WARN", "warning detail"),
        ]):
            result = run_doctor(no_color=True)
        self.assertIsInstance(result, int)

    def test_returns_1_on_fail(self) -> None:
        with patch("seal.doctor._run_all", return_value=[
            ("Foo", "FAIL", "down"),
        ]):
            result = run_doctor(no_color=True)
        self.assertEqual(result, 1)

    def test_returns_0_on_all_ok(self) -> None:
        with patch("seal.doctor._run_all", return_value=[
            ("Foo", "OK", "up"),
            ("Bar", "OK", "up"),
        ]):
            result = run_doctor(no_color=True)
        self.assertEqual(result, 0)

    def test_json_output_valid(self) -> None:
        rows = [("Foo", "OK", "detail"), ("Bar", "FAIL", "broken")]
        captured = StringIO()
        import sys
        with patch("seal.doctor._run_all", return_value=rows):
            with patch("sys.stdout", captured):
                run_doctor(json_out=True)
        output = json.loads(captured.getvalue())
        self.assertIsInstance(output, list)
        self.assertEqual(len(output), 2)
        self.assertEqual(output[0]["component"], "Foo")
        self.assertEqual(output[1]["status"], "FAIL")

    def test_table_renders_all_components(self) -> None:
        captured = StringIO()
        rows = [
            ("PostgreSQL SOUL", "OK",   "localhost:5433"),
            ("Qdrant",          "OK",   "localhost:6333"),
            ("Neo4j",           "WARN", "bolt refused"),
            ("Gateway :8765",   "FAIL", "unreachable"),
        ]
        with patch("sys.stdout", captured):
            print_table(rows, no_color=True)
        out = captured.getvalue()
        self.assertIn("PostgreSQL SOUL", out)
        self.assertIn("WARN", out)
        self.assertIn("FAIL", out)


# ── CLI integration ───────────────────────────────────────────────────────────


class CliDoctorTests(unittest.TestCase):
    def test_cli_doctor_subcommand(self) -> None:
        from seal.cli import main
        with patch("seal.doctor._run_all", return_value=[
            ("PostgreSQL SOUL", "OK", "ok"),
        ]):
            result = main(["doctor", "--no-color"])
        self.assertIsInstance(result, int)

    def test_cli_doctor_json_flag(self) -> None:
        from seal.cli import main
        captured = StringIO()
        with patch("seal.doctor._run_all", return_value=[("Foo", "OK", "ok")]):
            with patch("sys.stdout", captured):
                main(["doctor", "--json"])
        output = json.loads(captured.getvalue())
        self.assertIsInstance(output, list)


if __name__ == "__main__":
    unittest.main()
