from __future__ import annotations

from subprocess import CompletedProcess

from scripts import seal_agent_stability_guard as guard


def _heartbeat(monkeypatch, tmp_path, payload, runtimes=()):
    path = tmp_path / "heartbeat.json"
    path.write_text(__import__("json").dumps(payload), encoding="utf-8")
    monkeypatch.setattr(guard, "heartbeat_path", lambda agent: path)
    monkeypatch.setattr(guard, "runtime_processes", lambda agent, rows: list(runtimes))
    monkeypatch.setattr(guard, "process_alive", lambda pid: True)
    return guard.check_heartbeat("JARVIS", [])


def test_runtime_status_unique_is_the_only_green_state(monkeypatch, tmp_path):
    runtime = guard.Proc(123, 1, "claude", "claude --name JARVIS")

    class FakeProcPath:
        def read_text(self, **kwargs):
            return "claude\n"

        def read_bytes(self):
            return b"SEAL_AGENT=JARVIS\0"

    monkeypatch.setattr(guard, "Path", lambda value: FakeProcPath())
    result = _heartbeat(
        monkeypatch,
        tmp_path,
        {
            "alive": True,
            "process_pid": 123,
            "runtime_detection_status": "present_unique",
        },
        (runtime,),
    )
    assert result["ok"] is True
    assert result["runtime_detection_status"] == "present_unique"
    assert result["issues"] == []


def test_runtime_status_ambiguous_proves_presence_not_identity(monkeypatch, tmp_path):
    runtimes = (
        guard.Proc(123, 1, "claude", "claude --name JARVIS"),
        guard.Proc(456, 1, "claude", "claude --name JARVIS"),
    )
    result = _heartbeat(
        monkeypatch,
        tmp_path,
        {
            "alive": True,
            "process_pid": 0,
            "runtime_detection_status": "present_ambiguous",
        },
        runtimes,
    )
    assert result["ok"] is False
    assert result["runtime_detection_status"] == "present_ambiguous"
    assert result["issues"] == [
        "JARVIS: heartbeat runtime ambiguo; presencia sin identidad única"
    ]


def test_runtime_status_absent_is_not_indeterminate(monkeypatch, tmp_path):
    result = _heartbeat(
        monkeypatch,
        tmp_path,
        {"alive": False, "process_pid": 0, "runtime_detection_status": "absent"},
    )
    assert result["runtime_detection_status"] == "absent"
    assert result["issues"] == ["JARVIS: heartbeat runtime ausente"]


def test_runtime_status_indeterminate_does_not_claim_dead(monkeypatch, tmp_path):
    result = _heartbeat(
        monkeypatch,
        tmp_path,
        {
            "alive": False,
            "process_pid": 0,
            "runtime_detection_status": "indeterminate",
        },
    )
    assert result["runtime_detection_status"] == "indeterminate"
    assert result["issues"] == [
        "JARVIS: heartbeat runtime indeterminado; no afirma presencia ni ausencia"
    ]
    assert all("alive=false" not in issue for issue in result["issues"])


def test_legacy_runtime_label_preserves_indeterminate(monkeypatch, tmp_path):
    result = _heartbeat(
        monkeypatch,
        tmp_path,
        {
            "alive": False,
            "process_pid": 0,
            "runtime": "indeterminado_1_ilegibles",
        },
    )
    assert result["runtime_detection_status"] == "indeterminate"
    assert result["runtime_detection_status_source"] == "legacy_runtime"


def test_invalid_explicit_status_fails_closed_without_relabeling(monkeypatch, tmp_path):
    result = _heartbeat(
        monkeypatch,
        tmp_path,
        {"alive": False, "process_pid": 0, "runtime_detection_status": "green"},
    )
    assert result["runtime_detection_status"] == "green"
    assert result["issues"] == [
        "JARVIS: runtime_detection_status inválido=green"
    ]


def test_legacy_ambiguous_label_preserves_presence(monkeypatch, tmp_path):
    runtimes = (
        guard.Proc(123, 1, "claude", "claude --name JARVIS"),
        guard.Proc(456, 1, "claude", "claude --name JARVIS"),
    )
    result = _heartbeat(
        monkeypatch,
        tmp_path,
        {"alive": True, "process_pid": 0, "runtime": "ambiguo_2"},
        runtimes,
    )
    assert result["runtime_detection_status"] == "present_ambiguous"
    assert result["runtime_detection_status_source"] == "legacy_runtime"
    assert result["issues"] == [
        "JARVIS: heartbeat runtime ambiguo; presencia sin identidad única"
    ]


def test_ambiguous_claim_requires_multiple_observed_runtimes(monkeypatch, tmp_path):
    result = _heartbeat(
        monkeypatch,
        tmp_path,
        {
            "alive": True,
            "process_pid": 0,
            "runtime_detection_status": "present_ambiguous",
        },
        (guard.Proc(123, 1, "claude", "claude --name JARVIS"),),
    )
    assert result["issues"] == [
        "JARVIS: heartbeat runtime ambiguo; presencia sin identidad única",
        "JARVIS: heartbeat declaró ambigüedad pero el guard observó pids=[123]",
    ]


def test_absent_claim_rejects_observed_runtime(monkeypatch, tmp_path):
    result = _heartbeat(
        monkeypatch,
        tmp_path,
        {"alive": False, "process_pid": 0, "runtime_detection_status": "absent"},
        (guard.Proc(123, 1, "claude", "claude --name JARVIS"),),
    )
    assert result["issues"] == [
        "JARVIS: heartbeat runtime ausente",
        "JARVIS: heartbeat declaró ausencia pero el guard observó pids=[123]",
    ]


def test_present_unique_rejects_false_alive_projection(monkeypatch, tmp_path):
    runtime = guard.Proc(123, 1, "claude", "claude --name JARVIS")

    class FakeProcPath:
        def read_text(self, **kwargs):
            return "claude\n"

        def read_bytes(self):
            return b"SEAL_AGENT=JARVIS\0"

    monkeypatch.setattr(guard, "Path", lambda value: FakeProcPath())
    result = _heartbeat(
        monkeypatch,
        tmp_path,
        {
            "alive": False,
            "process_pid": 123,
            "runtime_detection_status": "present_unique",
        },
        (runtime,),
    )
    assert result["issues"] == [
        "JARVIS: proyección alive inconsistente con present_unique"
    ]


def test_ada_dead_pid_is_refreshed_once(monkeypatch):
    before = {
        "agent": "ADA",
        "ok": False,
        "pid": 111,
        "issues": ["ADA: heartbeat apunta a PID muerto 111"],
    }
    after = {
        "agent": "ADA",
        "ok": True,
        "pid": 222,
        "issues": [],
    }
    calls: list[list[str]] = []

    def fake_run(cmd, timeout=10):
        calls.append(cmd)
        return CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(guard, "run", fake_run)
    monkeypatch.setattr(guard, "check_heartbeat", lambda agent, rows: after)

    result, fixes = guard.refresh_ada_heartbeat_after_pid_rollover(before, [])

    assert result is after
    assert fixes == ["ADA: heartbeat refrescado tras rollover PID 111->222"]
    assert calls == [
        ["systemctl", "--user", "restart", "seal-ada-heartbeat.service"]
    ]


def test_ada_non_pid_issue_does_not_refresh(monkeypatch):
    before = {
        "agent": "ADA",
        "ok": False,
        "pid": 111,
        "issues": ["ADA: heartbeat stale age=999s"],
    }

    def unexpected_run(*args, **kwargs):
        raise AssertionError("no debe reiniciar el heartbeat")

    monkeypatch.setattr(guard, "run", unexpected_run)

    result, fixes = guard.refresh_ada_heartbeat_after_pid_rollover(before, [])

    assert result is before
    assert fixes == []


def test_ada_refresh_does_not_mask_real_outage(monkeypatch):
    before = {
        "agent": "ADA",
        "ok": False,
        "pid": 111,
        "issues": ["ADA: heartbeat apunta a PID muerto 111"],
    }
    after = {
        "agent": "ADA",
        "ok": False,
        "pid": None,
        "issues": ["ADA: heartbeat alive=false", "ADA: heartbeat sin PID de runtime"],
    }

    monkeypatch.setattr(
        guard,
        "run",
        lambda cmd, timeout=10: CompletedProcess(cmd, 0, "", ""),
    )
    monkeypatch.setattr(guard, "check_heartbeat", lambda agent, rows: after)

    result, fixes = guard.refresh_ada_heartbeat_after_pid_rollover(before, [])

    assert result is after
    assert fixes == []
