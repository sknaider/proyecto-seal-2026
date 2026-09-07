#!/usr/bin/env python3
"""DUM guard for the shared Claude Code OAuth session.

The four Claude principals share ``~/.claude/.credentials.json``.  This guard
never copies or prints credentials: it records only booleans and expiry times,
warns William before the refresh credential expires, detects logout/refresh
failure, and confirms recovery.  Interactive OAuth remains human-gated.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_BIN = Path.home() / ".local/bin/claude"
CREDENTIALS_PATH = Path.home() / ".claude/.credentials.json"
DEFAULT_STATE_DIR = Path.home() / ".local/state/seal/claude-auth-guard"
STATUS_PATH = Path("/tmp/seal_claude_auth_guard.status")
AGENTS = ("alice", "jarvis", "nexus", "fable")
WARNING_THRESHOLDS_HOURS = (24.0, 6.0, 1.0)
DOWN_REMINDER_SECONDS = 6 * 60 * 60
FAILURES_BEFORE_ALERT = 2


@dataclass(frozen=True)
class CredentialMetadata:
    has_access_token: bool
    has_refresh_token: bool
    access_expires_at: int
    refresh_expires_at: int
    subscription_type: str


@dataclass(frozen=True)
class GuardAssessment:
    status: str
    warning_bucket: str
    refresh_hours_left: float | None
    reason: str


def _epoch_seconds(value: object) -> int:
    if not isinstance(value, (int, float)) or value <= 0:
        return 0
    numeric = int(value)
    return numeric // 1000 if numeric > 10_000_000_000 else numeric


def load_credential_metadata(path: Path = CREDENTIALS_PATH) -> CredentialMetadata:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    oauth = payload.get("claudeAiOauth") if isinstance(payload, dict) else {}
    if not isinstance(oauth, dict):
        oauth = {}
    return CredentialMetadata(
        has_access_token=bool(oauth.get("accessToken")),
        has_refresh_token=bool(oauth.get("refreshToken")),
        access_expires_at=_epoch_seconds(oauth.get("expiresAt")),
        refresh_expires_at=_epoch_seconds(oauth.get("refreshTokenExpiresAt")),
        subscription_type=str(oauth.get("subscriptionType") or "unknown"),
    )


def read_auth_status(claude_bin: Path = CLAUDE_BIN) -> dict[str, object]:
    try:
        completed = subprocess.run(
            [str(claude_bin), "auth", "status"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        payload = json.loads(completed.stdout or "{}")
        if not isinstance(payload, dict):
            return {"probeError": "invalid_output"}
        if completed.returncode != 0 and "loggedIn" not in payload:
            return {"probeError": f"exit_{completed.returncode}"}
        return payload
    except subprocess.TimeoutExpired:
        return {"probeError": "timeout"}
    except OSError:
        return {"probeError": "unavailable"}
    except json.JSONDecodeError:
        return {"probeError": "invalid_json"}


def assess_auth(metadata: CredentialMetadata, auth_status: dict[str, object], now: float) -> GuardAssessment:
    probe_error = auth_status.get("probeError")
    if probe_error:
        return GuardAssessment(
            status="unknown",
            warning_bucket="probe_error",
            refresh_hours_left=(metadata.refresh_expires_at - now) / 3600 if metadata.refresh_expires_at else None,
            reason=f"auth probe failed: {probe_error}",
        )
    logged_in = auth_status.get("loggedIn") is True
    if not logged_in or not metadata.has_access_token or not metadata.has_refresh_token:
        return GuardAssessment(
            status="needs_login",
            warning_bucket="down",
            refresh_hours_left=(metadata.refresh_expires_at - now) / 3600 if metadata.refresh_expires_at else None,
            reason="OAuth is not logged in or the shared credential store is empty",
        )

    refresh_hours_left = None
    warning_bucket = "healthy"
    if metadata.refresh_expires_at:
        refresh_hours_left = (metadata.refresh_expires_at - now) / 3600
        if refresh_hours_left <= 0:
            return GuardAssessment("needs_login", "down", refresh_hours_left, "refresh credential expired")
        for threshold in WARNING_THRESHOLDS_HOURS:
            if refresh_hours_left <= threshold:
                warning_bucket = f"warn_{int(threshold)}h"
    return GuardAssessment("healthy", warning_bucket, refresh_hours_left, "authenticated")


def debounce_failure(
    assessment: GuardAssessment,
    state: dict[str, object],
) -> tuple[GuardAssessment, int]:
    """Require two consecutive bad probes before declaring the shared auth down."""
    if assessment.status not in {"needs_login", "unknown"}:
        return assessment, 0
    failures = int(state.get("consecutive_failures") or 0) + 1
    if failures >= FAILURES_BEFORE_ALERT:
        return assessment, failures
    return (
        GuardAssessment(
            status="suspect",
            warning_bucket="pending_confirmation",
            refresh_hours_left=assessment.refresh_hours_left,
            reason=f"{assessment.reason}; awaiting confirmation",
        ),
        failures,
    )


def live_tmux_agents() -> list[str]:
    live: list[str] = []
    for agent in AGENTS:
        completed = subprocess.run(
            ["tmux", "-L", f"seal-{agent}", "has-session", "-t", f"seal-{agent}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if completed.returncode == 0:
            live.append(agent.upper())
    return live


def load_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def should_notify(assessment: GuardAssessment, state: dict[str, object], now: float) -> bool:
    previous_status = state.get("status")
    previous_bucket = state.get("warning_bucket")
    if previous_status is None:
        return assessment.status not in {"healthy", "suspect"} or assessment.warning_bucket not in {
            "healthy",
            "pending_confirmation",
        }
    if assessment.status == "suspect":
        return False
    if assessment.status == "healthy" and previous_status == "suspect":
        return False
    if assessment.status != previous_status or assessment.warning_bucket != previous_bucket:
        return True
    if assessment.status in {"needs_login", "unknown"}:
        last_alert = float(state.get("last_alert_at") or 0)
        return now - last_alert >= DOWN_REMINDER_SECONDS
    return False


def notification_text(assessment: GuardAssessment, live_agents: list[str]) -> str:
    live = ", ".join(live_agents) if live_agents else "ninguno confirmado"
    if assessment.status == "needs_login":
        return (
            "🛡️ [DUM/AUTH] OAuth compartido de Claude requiere login. "
            f"Tmux preservados: {live}. No reiniciaré ni borraré contextos. "
            "William: ejecuta /login en UNA terminal Claude y completa el navegador; "
            "DUM verificará automáticamente la recuperación."
        )
    if assessment.status == "unknown":
        return (
            "🔴 [DUM/AUTH] El healthcheck de autenticación de Claude falló dos veces consecutivas. "
            f"Tmux preservados: {live}. No reiniciaré sesiones ni ejecutaré /login automáticamente. "
            "Revisar el servicio seal-claude-auth-guard y la disponibilidad de Claude Code."
        )
    if assessment.warning_bucket.startswith("warn_"):
        hours = max(0.0, assessment.refresh_hours_left or 0.0)
        return (
            f"🟡 [DUM/AUTH] El refresh OAuth compartido de Claude vence en aproximadamente {hours:.1f} h. "
            "Conviene completar /login en una sola terminal antes del vencimiento; los contextos no se reinician."
        )
    return (
        f"✅ [DUM/AUTH] OAuth de Claude recuperado y verificado. Tmux preservados: {live}. "
        "Los agentes pueden continuar sin reiniciar; si uno conserva el aviso, basta reintentar allí."
    )


def notification_type(assessment: GuardAssessment) -> str:
    return "status" if assessment.status == "healthy" else "alert"


def status_label(assessment: GuardAssessment, raw_assessment: GuardAssessment) -> str:
    """Expose the debounced state without hiding the raw failure class."""
    if assessment.status == "suspect":
        return f"suspect({raw_assessment.status})"
    return assessment.status


def send_as_dum(message: str, idempotency_key: str, message_type: str) -> tuple[bool, str]:
    if message_type not in {"alert", "status"}:
        raise ValueError(f"unsupported proactive message type: {message_type}")
    completed = subprocess.run(
        [
            str(PROJECT_ROOT / "scripts/seal_send.py"),
            "DUM",
            "William",
            message,
            "--channel",
            "web_chat",
            "--type",
            message_type,
            "--proactive",
            "--idempotency-key",
            idempotency_key,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    diagnostic = (completed.stderr or completed.stdout or "").strip().splitlines()
    last_line = diagnostic[-1][:300] if diagnostic else ""
    # Any non-zero exit is a real delivery failure. In particular, HTTP 422 may
    # mean in_reply_to_required; it must never be recorded as delivered.
    delivered = completed.returncode == 0
    return delivered, ("" if delivered else last_line)


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_guard(*, state_dir: Path, notify: bool, now: float | None = None) -> dict[str, object]:
    now = time.time() if now is None else now
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "guard.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path = state_dir / "state.json"
        previous = load_state(state_path)
        metadata = load_credential_metadata()
        auth_status = read_auth_status()
        raw_assessment = assess_auth(metadata, auth_status, now)
        assessment, consecutive_failures = debounce_failure(raw_assessment, previous)
        live_agents = live_tmux_agents()
        emit = should_notify(assessment, previous, now)
        message = notification_text(assessment, live_agents)

        notified = False
        notification_error = ""
        if emit and notify:
            key_epoch = (
                int(now // DOWN_REMINDER_SECONDS)
                if assessment.status in {"needs_login", "unknown"}
                else int(now)
            )
            notified, notification_error = send_as_dum(
                message,
                f"dum-claude-auth-{assessment.warning_bucket}-{key_epoch}",
                notification_type(assessment),
            )

        state: dict[str, object] = {
            "checked_at": int(now),
            "status": assessment.status,
            "warning_bucket": assessment.warning_bucket,
            "reason": assessment.reason,
            "raw_status": raw_assessment.status,
            "consecutive_failures": consecutive_failures,
            "refresh_expires_at": metadata.refresh_expires_at,
            "refresh_hours_left": round(assessment.refresh_hours_left, 2) if assessment.refresh_hours_left is not None else None,
            "subscription_type": metadata.subscription_type,
            "live_tmux_agents": live_agents,
            "last_alert_at": int(now) if notified else int(previous.get("last_alert_at") or 0),
            "last_notification_ok": notified if emit and notify else previous.get("last_notification_ok"),
            "last_notification_error": notification_error if emit and notify and not notified else "",
        }
        write_json_atomic(state_path, state)
        STATUS_PATH.write_text(
            f"status={status_label(assessment, raw_assessment)} "
            f"bucket={assessment.warning_bucket} live={','.join(live_agents)} "
            f"checked={int(now)}\n",
            encoding="utf-8",
        )
        return {**state, "notification_due": emit, "notification": message if emit else None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args()
    result = run_guard(state_dir=args.state_dir.expanduser(), notify=not args.no_notify)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
