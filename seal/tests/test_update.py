"""Tests for seal/update.py — self-update with automatic rollback.

Run:
    python3 -m pytest seal/tests/test_update.py -v
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from seal.update import (
    SealUpdater,
    UpdateConfig,
    UpdateResult,
    _apply_tarball,
    _find_top,
    _post_verify,
    _read_version,
    _verify_sha256,
    run_update,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_seal_home(tmp: Path, version: str = "1.0.0") -> Path:
    home = tmp / ".seal"
    home.mkdir()
    (home / "VERSION").write_text(version)
    pkg = home / "seal"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("# seal package\n")
    return home


def _make_tarball(tmp: Path, version: str = "1.1.0") -> tuple[Path, str]:
    """Build a minimal seal tarball and return (path, sha256hex)."""
    src = tmp / "release_src"
    src.mkdir()
    top = src / f"seal-{version}"
    top.mkdir()
    (top / "VERSION").write_text(version)
    pkg = top / "seal"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("# new seal\n")

    tarball = tmp / f"seal-{version}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(top, arcname=top.name)

    h = hashlib.sha256()
    with open(tarball, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return tarball, h.hexdigest()


# ── Fake HTTP release server ──────────────────────────────────────────────────


class _ReleaseHandler(BaseHTTPRequestHandler):
    tarball_path: Path
    sha256_hex: str
    version: str = "1.1.0"

    def do_GET(self) -> None:
        if self.path == "/manifest.json":
            name = self.tarball_path.name
            body = json.dumps({
                "version": self.version,
                "tarball": name,
                "sha256":  name + ".sha256",
            }).encode()
            self._respond(200, body, "application/json")
        elif self.path.endswith(".sha256"):
            body = (self.sha256_hex + "  " + self.tarball_path.name + "\n").encode()
            self._respond(200, body, "text/plain")
        elif self.path == "/" + self.tarball_path.name:
            body = self.tarball_path.read_bytes()
            self._respond(200, body, "application/octet-stream")
        else:
            self._respond(404, b"not found", "text/plain")

    def _respond(self, code: int, body: bytes, ct: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a: Any) -> None:
        pass


def _make_server(tarball: Path, sha256: str, version: str = "1.1.0") -> tuple[HTTPServer, int]:
    port = _free_port()

    class _H(_ReleaseHandler):
        pass

    _H.tarball_path = tarball
    _H.sha256_hex   = sha256
    _H.version      = version
    srv = HTTPServer(("127.0.0.1", port), _H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, port


# ── _read_version ─────────────────────────────────────────────────────────────


class ReadVersionTests(unittest.TestCase):
    def test_reads_version_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / "VERSION").write_text("2.3.4")
            self.assertEqual(_read_version(home), "2.3.4")

    def test_returns_unknown_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(_read_version(Path(d)), "unknown")


# ── _verify_sha256 ────────────────────────────────────────────────────────────


class VerifySha256Tests(unittest.TestCase):
    def test_passes_correct_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tb, sha = _make_tarball(Path(d))
            cs = Path(d) / "checksum.sha256"
            cs.write_text(sha + "  " + tb.name + "\n")
            ok, detail = _verify_sha256(tb, cs)
            self.assertTrue(ok)

    def test_fails_wrong_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tb, _ = _make_tarball(Path(d))
            cs = Path(d) / "checksum.sha256"
            cs.write_text("0" * 64 + "  " + tb.name + "\n")
            ok, detail = _verify_sha256(tb, cs)
            self.assertFalse(ok)
            self.assertIn("mismatch", detail)

    def test_fails_missing_checksum_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tb, _ = _make_tarball(Path(d))
            ok, detail = _verify_sha256(tb, Path(d) / "ghost.sha256")
            self.assertFalse(ok)


# ── _find_top ─────────────────────────────────────────────────────────────────


class FindTopTests(unittest.TestCase):
    def test_single_subdir(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "seal-1.0"
            sub.mkdir()
            self.assertEqual(_find_top(Path(d)), "seal-1.0")

    def test_multiple_entries_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a").mkdir()
            (Path(d) / "b").mkdir()
            self.assertIsNone(_find_top(Path(d)))


# ── _apply_tarball ────────────────────────────────────────────────────────────


class ApplyTarballTests(unittest.TestCase):
    def test_applies_new_version(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = _make_seal_home(Path(d), "1.0.0")
            tb, _ = _make_tarball(Path(d), "1.1.0")
            ok, detail = _apply_tarball(tb, home)
            self.assertTrue(ok, detail)
            self.assertEqual(_read_version(home), "1.1.0")
            self.assertTrue((home / "seal" / "__init__.py").exists())

    def test_preserves_profile_data(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = _make_seal_home(Path(d))
            profiles = home / "profiles" / "acme"
            profiles.mkdir(parents=True)
            (profiles / "config.toml").write_text("[agent]\nname='JARVIS'\n")

            tb, _ = _make_tarball(Path(d), "1.2.0")
            _apply_tarball(tb, home)
            self.assertTrue((profiles / "config.toml").exists())

    def test_fails_on_bad_tarball(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = _make_seal_home(Path(d))
            bad = Path(d) / "bad.tar.gz"
            bad.write_bytes(b"not a tarball")
            ok, detail = _apply_tarball(bad, home)
            self.assertFalse(ok)


# ── _post_verify ──────────────────────────────────────────────────────────────


class PostVerifyTests(unittest.TestCase):
    def test_ok_when_init_present(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = _make_seal_home(Path(d))
            ok, detail = _post_verify(home)
            self.assertTrue(ok)

    def test_fail_when_init_missing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / ".seal"
            home.mkdir()
            (home / "seal").mkdir()
            ok, detail = _post_verify(home)
            self.assertFalse(ok)
            self.assertIn("missing", detail)


# ── Backup / rollback ─────────────────────────────────────────────────────────


class BackupRollbackTests(unittest.TestCase):
    def test_backup_creates_copy(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = _make_seal_home(Path(d))
            cfg  = UpdateConfig(seal_home=home)
            up   = SealUpdater(cfg)
            ok, detail = up._do_backup()
            self.assertTrue(ok, detail)
            backup = home.parent / (home.name + ".bak")
            self.assertTrue(backup.exists())
            self.assertTrue((backup / "VERSION").exists())

    def test_rollback_restores_from_backup(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = _make_seal_home(Path(d), "1.0.0")
            cfg  = UpdateConfig(seal_home=home)
            up   = SealUpdater(cfg)
            up._do_backup()
            # Corrupt home
            (home / "VERSION").write_text("corrupted")
            result = UpdateResult()
            up._rollback(result)
            self.assertTrue(result.rolled_back)
            self.assertEqual(_read_version(home), "1.0.0")

    def test_rollback_reports_no_backup(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = _make_seal_home(Path(d))
            cfg  = UpdateConfig(seal_home=home)
            up   = SealUpdater(cfg)
            result = UpdateResult()
            up._rollback(result)
            self.assertFalse(result.rolled_back)
            failed = [s for s in result.steps if not s.ok]
            self.assertTrue(failed)


# ── Full update flow (with fake HTTP server) ──────────────────────────────────


class FullUpdateFlowTests(unittest.TestCase):
    def test_successful_update(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = _make_seal_home(Path(d), "1.0.0")
            tb, sha = _make_tarball(Path(d), "1.1.0")
            srv, port = _make_server(tb, sha, "1.1.0")
            try:
                result = run_update(
                    url=f"http://127.0.0.1:{port}",
                    seal_home=home,
                    no_color=True,
                )
                self.assertEqual(result, 0)
                self.assertEqual(_read_version(home), "1.1.0")
            finally:
                srv.shutdown()

    def test_rollback_on_bad_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = _make_seal_home(Path(d), "1.0.0")
            tb, _ = _make_tarball(Path(d), "1.1.0")
            srv, port = _make_server(tb, "0" * 64, "1.1.0")
            try:
                rc = run_update(
                    url=f"http://127.0.0.1:{port}",
                    seal_home=home,
                    no_color=True,
                )
                self.assertEqual(rc, 1)
                # rolled back — old version preserved
                self.assertEqual(_read_version(home), "1.0.0")
            finally:
                srv.shutdown()

    def test_dry_run_does_not_modify_home(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = _make_seal_home(Path(d), "1.0.0")
            tb, sha = _make_tarball(Path(d), "1.1.0")
            srv, port = _make_server(tb, sha, "1.1.0")
            try:
                run_update(
                    url=f"http://127.0.0.1:{port}",
                    seal_home=home,
                    dry_run=True,
                    no_color=True,
                )
                self.assertEqual(_read_version(home), "1.0.0")
            finally:
                srv.shutdown()

    def test_check_only_returns_version_info(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = _make_seal_home(Path(d), "1.0.0")
            tb, sha = _make_tarball(Path(d), "2.0.0")
            srv, port = _make_server(tb, sha, "2.0.0")
            captured = StringIO()
            try:
                with patch("sys.stdout", captured):
                    run_update(
                        url=f"http://127.0.0.1:{port}",
                        seal_home=home,
                        check_only=True,
                        json_out=True,
                    )
                out = json.loads(captured.getvalue())
                self.assertEqual(out["new_version"], "2.0.0")
                self.assertEqual(out["old_version"], "1.0.0")
                self.assertEqual(_read_version(home), "1.0.0")
            finally:
                srv.shutdown()

    def test_fail_when_server_unreachable(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = _make_seal_home(Path(d), "1.0.0")
            port = _free_port()
            rc = run_update(
                url=f"http://127.0.0.1:{port}",
                seal_home=home,
                no_color=True,
            )
            self.assertEqual(rc, 1)


# ── JSON output ───────────────────────────────────────────────────────────────


class JsonOutputTests(unittest.TestCase):
    def test_json_output_structure(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = _make_seal_home(Path(d), "1.0.0")
            tb, sha = _make_tarball(Path(d), "1.1.0")
            srv, port = _make_server(tb, sha, "1.1.0")
            captured = StringIO()
            try:
                with patch("sys.stdout", captured):
                    run_update(
                        url=f"http://127.0.0.1:{port}",
                        seal_home=home,
                        json_out=True,
                    )
                out = json.loads(captured.getvalue())
                self.assertIn("success", out)
                self.assertIn("old_version", out)
                self.assertIn("new_version", out)
                self.assertIn("steps", out)
                self.assertIsInstance(out["steps"], list)
            finally:
                srv.shutdown()


# ── CLI integration ───────────────────────────────────────────────────────────


class CliUpdateTests(unittest.TestCase):
    def test_cli_update_check_flag(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = _make_seal_home(Path(d), "1.0.0")
            tb, sha = _make_tarball(Path(d), "1.1.0")
            srv, port = _make_server(tb, sha, "1.1.0")
            from seal.cli import main
            captured = StringIO()
            try:
                with patch("sys.stdout", captured):
                    rc = main([
                        "update",
                        f"--url=http://127.0.0.1:{port}",
                        f"--seal-home={home}",
                        "--check",
                        "--json",
                    ])
                out = json.loads(captured.getvalue())
                self.assertEqual(out["new_version"], "1.1.0")
            finally:
                srv.shutdown()


if __name__ == "__main__":
    unittest.main()
