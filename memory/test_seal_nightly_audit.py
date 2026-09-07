from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seal_nightly_audit  # noqa: E402


def _run(stdout: str, returncode: int = 0) -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


def test_disabled_optional_bridges_do_not_make_audit_red() -> None:
    def fake_run(argv, **_kwargs):
        if argv[2] == "is-enabled":
            return _run("disabled\n", 1)
        if argv[-1] in seal_nightly_audit.RETIRED_SERVICES:
            return _run("inactive\n", 3)
        return _run("active\n")

    with patch("seal_nightly_audit.subprocess.run", side_effect=fake_run):
        result = seal_nightly_audit.check_services()

    assert result["findings"] == []
    assert result["metrics"]["services"]["seal-mm-bridge.service"].startswith("disabled")


def test_resurrected_retired_bridge_is_warning() -> None:
    def fake_run(argv, **_kwargs):
        if argv[-1] == "seal-event-bus.service":
            return _run("enabled\n" if argv[2] == "is-enabled" else "active\n")
        if argv[2] == "is-enabled":
            return _run("disabled\n", 1)
        if argv[-1] in seal_nightly_audit.RETIRED_SERVICES:
            return _run("inactive\n", 3)
        return _run("active\n")

    with patch("seal_nightly_audit.subprocess.run", side_effect=fake_run):
        result = seal_nightly_audit.check_services()

    assert {
        "sev": "WARN",
        "msg": "Retired service seal-event-bus.service resurrected: active=active, enabled=enabled",
    } in result["findings"]


def test_enabled_but_inactive_optional_bridge_is_warning_not_critical() -> None:
    def fake_run(argv, **_kwargs):
        if argv[2] == "is-enabled":
            return _run("enabled\n")
        if argv[-1] == "seal-mm-bridge.service":
            return _run("inactive\n", 3)
        return _run("active\n")

    with patch("seal_nightly_audit.subprocess.run", side_effect=fake_run):
        result = seal_nightly_audit.check_services()

    bridge_findings = [
        item for item in result["findings"] if "seal-mm-bridge.service" in item["msg"]
    ]
    assert bridge_findings == [
        {"sev": "WARN", "msg": "Enabled optional service seal-mm-bridge.service is 'inactive'"}
    ]


def test_inactive_critical_service_remains_critical() -> None:
    def fake_run(argv, **_kwargs):
        if argv[2] == "is-enabled":
            return _run("disabled\n", 1)
        if argv[-1] == "seal-mcp-server.service":
            return _run("inactive\n", 3)
        return _run("active\n")

    with patch("seal_nightly_audit.subprocess.run", side_effect=fake_run):
        result = seal_nightly_audit.check_services()

    assert {
        "sev": "CRIT",
        "msg": "Critical service seal-mcp-server.service is 'inactive'",
    } in result["findings"]
