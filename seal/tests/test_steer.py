"""Tests for seal/steer.py check_steer helper.

Run:
    python3 -m pytest seal/tests/test_steer.py -v
"""
from __future__ import annotations

import json
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch

from seal.steer import check_steer


def _mock_response(body: dict, status: int = 200):
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class CheckSteerTests(unittest.TestCase):
    def test_returns_steer_when_present(self) -> None:
        payload = {
            "steer": {
                "from": "William",
                "message": "agrega validación RUC",
                "timestamp": "2026-04-28T12:00:00Z",
            }
        }
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = check_steer("ADA")
        self.assertIsNotNone(result)
        self.assertEqual(result["from"], "William")
        self.assertEqual(result["message"], "agrega validación RUC")

    def test_returns_none_when_no_steer_key(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_response({})):
            result = check_steer("ADA")
        self.assertIsNone(result)

    def test_returns_none_when_steer_falsy(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_response({"steer": None})):
            result = check_steer("ADA")
        self.assertIsNone(result)

    def test_returns_none_on_404(self) -> None:
        import urllib.error
        exc = urllib.error.HTTPError(url="", code=404, msg="Not Found", hdrs=None, fp=None)
        with patch("urllib.request.urlopen", side_effect=exc):
            result = check_steer("NEXUS")
        self.assertIsNone(result)

    def test_returns_none_on_500(self) -> None:
        import urllib.error
        exc = urllib.error.HTTPError(url="", code=500, msg="Server Error", hdrs=None, fp=None)
        with patch("urllib.request.urlopen", side_effect=exc):
            result = check_steer("JARVIS")
        self.assertIsNone(result)

    def test_returns_none_when_server_unreachable(self) -> None:
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("no server")):
            result = check_steer("ADA")
        self.assertIsNone(result)

    def test_custom_base_url_used(self) -> None:
        """Verify the agent name is included in the URL."""
        payload = {"steer": {"from": "William", "message": "ok", "timestamp": "t"}}
        captured = []

        def _capture(req, timeout=None):
            captured.append(req.full_url)
            return _mock_response(payload)

        with patch("urllib.request.urlopen", side_effect=_capture):
            check_steer("ALICE", base_url="http://localhost:9999")

        self.assertIn("ALICE", captured[0])
        self.assertIn("9999", captured[0])


if __name__ == "__main__":
    unittest.main()
