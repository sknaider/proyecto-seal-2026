#!/usr/bin/env python3
"""Tests del SOUL Safety Governor — con CONTROL NEGATIVO.

Lección (cura SOUL, NEXUS 2026-06-09): un check que solo prueba el caso bueno puede
dar falso-PASS. Cada invariante se prueba en DOS direcciones: source curado → PASS,
source roto (invariante violada) → FAIL. Si el check no FALLA cuando debe, es inútil.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import soul_safety_governor as sg


# ── Fixtures de source: uno CURADO (cumple) y uno ROTO (viola) por invariante ──

CURED_SRC = """
def _session_key():
    sid = getattr(session, "_seal_sid", None)
    if sid is None:
        sid = _uuid.uuid4().hex
        setattr(session, "_seal_sid", sid)
    return sid

def _read_agent_token(agent): ...
def _seal_identity_mode(): ...

async def memory_gateway(action):
    await _privacy_check(c, t, tool, {})

async def soul_gateway(action):
    await _privacy_check(c, t, tool, {})

async def connectome_gateway(action):
    await _privacy_check(c, t, tool, {})

async def system_gateway(action):
    await _privacy_check(c, t, tool, {})

async def _resolve_memory_owner(mid): ...
async def _foreign_owner_by_ids(c, ids): ...
_TOOL_CATEGORY = {"memory_invalidate": "PRIVATE-WRITE"}
__OWNER_UNRESOLVED__ = "x"

async def _log_privacy(caller, target, tool, outcome, reason, sid):
    await pool.execute(
        "INSERT INTO soul_v3.event_log (agent, event_type, content, metadata) "
        "VALUES ($1, 'system', $2, $3::jsonb)", caller, summary,
        json.dumps({"kind": "privacy_check"}))
"""

# ROTO: identidad por id() crudo, un gateway sin check, sin owner-resolution, audit a 'payload'.
BROKEN_SRC = """
def _session_key():
    return id(ctx.request_context.session)

async def memory_gateway(action):
    await _privacy_check(c, t, tool, {})

async def soul_gateway(action):
    return await handler()

async def connectome_gateway(action):
    return await handler()

async def system_gateway(action):
    return await handler()

async def _log_privacy(caller, target, tool, outcome, reason, sid):
    await pool.execute(
        "INSERT INTO soul_v3.event_log (agent, event_type, payload) "
        "VALUES ($1, 'privacy_check', $2::jsonb)", caller, x)
"""


def test_inv6_pass_on_cured():
    assert sg.check_inv6_identity_not_from_payload(CURED_SRC).status == sg.PASS


def test_inv6_fail_on_raw_id():
    # CONTROL NEGATIVO: id() crudo + sin token-aware → debe FALLAR
    assert sg.check_inv6_identity_not_from_payload(BROKEN_SRC).status == sg.FAIL


def test_inv5_pass_on_cured():
    assert sg.check_inv5_no_foreign_private(CURED_SRC).status == sg.PASS


def test_inv5_fail_when_gateway_bypasses():
    # CONTROL NEGATIVO: 3 gateways sin _privacy_check → debe FALLAR
    r = sg.check_inv5_no_foreign_private(BROKEN_SRC)
    assert r.status == sg.FAIL
    assert "soul_gateway" in r.evidence or "connectome_gateway" in r.evidence


def test_inv4_pass_on_cured():
    assert sg.check_inv4_destructive_guarded(CURED_SRC).status == sg.PASS


def test_inv4_fail_without_owner_resolution():
    assert sg.check_inv4_destructive_guarded(BROKEN_SRC).status == sg.FAIL


def test_audit_pass_on_real_columns():
    assert sg.check_audit_trail_writes(CURED_SRC).status == sg.PASS


def test_audit_fail_on_payload_column():
    # CONTROL NEGATIVO: INSERT a columna 'payload' inexistente → debe FALLAR
    assert sg.check_audit_trail_writes(BROKEN_SRC).status == sg.FAIL


def test_audit_not_fooled_by_comment_mentioning_payload():
    # Regresión: la palabra 'payload' en un COMENTARIO no debe causar falso-FAIL.
    src_with_comment = CURED_SRC.replace(
        'async def _log_privacy',
        '# nota: la tabla NO tiene columna payload\nasync def _log_privacy')
    assert sg.check_audit_trail_writes(src_with_comment).status == sg.PASS


def test_audit_report_structure():
    report = sg.audit(skip_network=True)
    assert report["verdict"] in ("SAFE", "UNSAFE")
    assert "safe_to_act_autonomously" in report
    assert isinstance(report["invariants"], list)
    assert report["summary"]["pass"] + report["summary"]["fail"] + report["summary"]["skip"] \
        == len(report["invariants"])


def test_is_safe_to_act_returns_tuple():
    safe, reasons = sg.is_safe_to_act(skip_network=True)
    assert isinstance(safe, bool)
    assert isinstance(reasons, list)
    # si es safe, no debe haber razones; si no, debe explicar
    assert (safe and not reasons) or (not safe and reasons)


def test_identity_mode_pass_when_logic_present():
    assert sg.check_identity_mode(CURED_SRC + "\ndef _seal_identity_mode(): pass\n# ENFORCE").status == sg.PASS


def test_identity_mode_fail_without_logic():
    assert sg.check_identity_mode("def nothing(): pass").status == sg.FAIL


def test_identity_mode_reads_file_before_env():
    old_tokens_dir = os.environ.get("SEAL_TOKENS_DIR")
    old_mode_file = os.environ.get("SEAL_IDENTITY_MODE_FILE")
    old_env_mode = os.environ.get("SEAL_IDENTITY_MODE")
    old_home = os.environ.get("HOME")
    with tempfile.TemporaryDirectory() as d:
        home = Path(d, "home")
        home.mkdir()
        Path(d, "identity_mode").write_text("MIGRATE\n", encoding="utf-8")
        os.environ["HOME"] = str(home)
        os.environ["SEAL_TOKENS_DIR"] = d
        os.environ.pop("SEAL_IDENTITY_MODE_FILE", None)
        os.environ["SEAL_IDENTITY_MODE"] = "OFF"
        mode, source = sg._identity_mode_value()
    if old_tokens_dir is None:
        os.environ.pop("SEAL_TOKENS_DIR", None)
    else:
        os.environ["SEAL_TOKENS_DIR"] = old_tokens_dir
    if old_mode_file is None:
        os.environ.pop("SEAL_IDENTITY_MODE_FILE", None)
    else:
        os.environ["SEAL_IDENTITY_MODE_FILE"] = old_mode_file
    if old_env_mode is None:
        os.environ.pop("SEAL_IDENTITY_MODE", None)
    else:
        os.environ["SEAL_IDENTITY_MODE"] = old_env_mode
    if old_home is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = old_home
    assert mode == "MIGRATE"
    assert source.startswith("file:")


def test_identity_mode_env_fallback_without_file():
    old_tokens_dir = os.environ.get("SEAL_TOKENS_DIR")
    old_mode_file = os.environ.get("SEAL_IDENTITY_MODE_FILE")
    old_env_mode = os.environ.get("SEAL_IDENTITY_MODE")
    old_disable_tmp = os.environ.get("SEAL_DISABLE_LEGACY_TMP_TOKENS")
    old_home = os.environ.get("HOME")
    with tempfile.TemporaryDirectory() as d:
        home = Path(d, "home")
        home.mkdir()
        os.environ["HOME"] = str(home)
        os.environ["SEAL_TOKENS_DIR"] = d
        os.environ["SEAL_IDENTITY_MODE_FILE"] = str(Path(d, "missing_identity_mode"))
        os.environ["SEAL_IDENTITY_MODE"] = "ENFORCE"
        os.environ["SEAL_DISABLE_LEGACY_TMP_TOKENS"] = "1"
        mode, source = sg._identity_mode_value()
    if old_tokens_dir is None:
        os.environ.pop("SEAL_TOKENS_DIR", None)
    else:
        os.environ["SEAL_TOKENS_DIR"] = old_tokens_dir
    if old_mode_file is None:
        os.environ.pop("SEAL_IDENTITY_MODE_FILE", None)
    else:
        os.environ["SEAL_IDENTITY_MODE_FILE"] = old_mode_file
    if old_env_mode is None:
        os.environ.pop("SEAL_IDENTITY_MODE", None)
    else:
        os.environ["SEAL_IDENTITY_MODE"] = old_env_mode
    if old_disable_tmp is None:
        os.environ.pop("SEAL_DISABLE_LEGACY_TMP_TOKENS", None)
    else:
        os.environ["SEAL_DISABLE_LEGACY_TMP_TOKENS"] = old_disable_tmp
    if old_home is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = old_home
    assert mode == "ENFORCE"
    assert source == "env:SEAL_IDENTITY_MODE"


def test_identity_mode_explicit_file_wins_over_tokens_dir():
    old_tokens_dir = os.environ.get("SEAL_TOKENS_DIR")
    old_mode_file = os.environ.get("SEAL_IDENTITY_MODE_FILE")
    old_env_mode = os.environ.get("SEAL_IDENTITY_MODE")
    with tempfile.TemporaryDirectory() as d:
        explicit = Path(d, "control_mode")
        token_dir = Path(d, "tokens")
        token_dir.mkdir()
        explicit.write_text("ENFORCE\n", encoding="utf-8")
        Path(token_dir, "identity_mode").write_text("OFF\n", encoding="utf-8")
        os.environ["SEAL_IDENTITY_MODE_FILE"] = str(explicit)
        os.environ["SEAL_TOKENS_DIR"] = str(token_dir)
        os.environ["SEAL_IDENTITY_MODE"] = "MIGRATE"
        mode, source = sg._identity_mode_value()
    if old_tokens_dir is None:
        os.environ.pop("SEAL_TOKENS_DIR", None)
    else:
        os.environ["SEAL_TOKENS_DIR"] = old_tokens_dir
    if old_mode_file is None:
        os.environ.pop("SEAL_IDENTITY_MODE_FILE", None)
    else:
        os.environ["SEAL_IDENTITY_MODE_FILE"] = old_mode_file
    if old_env_mode is None:
        os.environ.pop("SEAL_IDENTITY_MODE", None)
    else:
        os.environ["SEAL_IDENTITY_MODE"] = old_env_mode
    assert mode == "ENFORCE"
    assert source.endswith("control_mode")


def test_action_deny_cross_agent_private():
    v = sg.classify_action(
        {"description": "leer diario", "target_agent": "ALICE", "scope": "agent"}, caller="NEXUS")
    assert v.decision == sg.DENY


def test_action_allow_own_routine():
    v = sg.classify_action(
        {"description": "leer mis propias tareas", "target_agent": "NEXUS"}, caller="NEXUS")
    assert v.decision == sg.ALLOW


def test_action_destructive_requires_william():
    v = sg.classify_action({"description": "delete memorias viejas", "kind": "cleanup"})
    assert v.decision == sg.REQUIRE_WILLIAM
    assert any("COUNT" in r for r in v.requires)


def test_action_destructive_with_safeguards_allowed():
    v = sg.classify_action({
        "description": "delete memorias viejas", "count": 12, "scope": "NEXUS test",
        "has_explicit_ok": True})
    assert v.decision == sg.ALLOW


def test_action_infra_requires_william():
    v = sg.classify_action({"description": "systemctl restart mcp_server en produccion"})
    assert v.decision == sg.REQUIRE_WILLIAM
    assert any("rollback" in r for r in v.requires)


def test_action_infra_with_rollback_and_ok():
    v = sg.classify_action({
        "description": "systemctl restart seal-mcp-server",
        "has_rollback": True, "has_explicit_ok": True})
    assert v.decision == sg.ALLOW


def test_action_cross_agent_with_consent_not_denied():
    v = sg.classify_action(
        {"description": "leer", "target_agent": "ALICE", "scope": "agent", "has_consent": True},
        caller="NEXUS")
    assert v.decision != sg.DENY


def _patch_systemctl(props: dict[str, dict[str, str]]):
    """Devuelve un _systemctl_prop falso a partir de {svc: {prop: value}}."""
    def fake(service, prop):
        svc = service.replace(".service", "")
        return props.get(svc, {}).get(prop)
    return fake


def test_resilience_pass_all_healthy(monkeypatch=None):
    orig = sg._systemctl_prop
    sg._systemctl_prop = _patch_systemctl({
        "seal-mcp-server": {"Type": "simple", "ActiveState": "active", "Restart": "on-failure"},
        "seal-chat": {"Type": "simple", "ActiveState": "active", "Restart": "on-failure"},
        "seal-bridge-nexus": {"Type": "simple", "ActiveState": "active", "Restart": "always"},
        "seal-infra-watchdog": {"Type": "oneshot", "ActiveState": "inactive", "Restart": "no"},
    })
    try:
        assert sg.check_resilience().status == sg.PASS
    finally:
        sg._systemctl_prop = orig


def test_resilience_fail_daemon_down():
    orig = sg._systemctl_prop
    sg._systemctl_prop = _patch_systemctl({
        "seal-mcp-server": {"Type": "simple", "ActiveState": "failed", "Restart": "on-failure"},
        "seal-chat": {"Type": "simple", "ActiveState": "active", "Restart": "on-failure"},
        "seal-bridge-nexus": {"Type": "simple", "ActiveState": "active", "Restart": "always"},
        "seal-infra-watchdog": {"Type": "oneshot", "ActiveState": "inactive", "Restart": "no"},
    })
    try:
        r = sg.check_resilience()
        assert r.status == sg.FAIL and r.severity == "critical"
    finally:
        sg._systemctl_prop = orig


def test_resilience_oneshot_failed_is_high_not_critical():
    orig = sg._systemctl_prop
    sg._systemctl_prop = _patch_systemctl({
        "seal-mcp-server": {"Type": "simple", "ActiveState": "active", "Restart": "on-failure"},
        "seal-chat": {"Type": "simple", "ActiveState": "active", "Restart": "on-failure"},
        "seal-bridge-nexus": {"Type": "simple", "ActiveState": "active", "Restart": "always"},
        "seal-infra-watchdog": {"Type": "oneshot", "ActiveState": "failed", "Restart": "no"},
    })
    try:
        r = sg.check_resilience()
        assert r.status == sg.FAIL and r.severity == "high"  # degradado, no outage
    finally:
        sg._systemctl_prop = orig


def test_resilience_daemon_no_autorestart_fails():
    orig = sg._systemctl_prop
    sg._systemctl_prop = _patch_systemctl({
        "seal-mcp-server": {"Type": "simple", "ActiveState": "active", "Restart": "no"},
        "seal-chat": {"Type": "simple", "ActiveState": "active", "Restart": "on-failure"},
        "seal-bridge-nexus": {"Type": "simple", "ActiveState": "active", "Restart": "always"},
        "seal-infra-watchdog": {"Type": "oneshot", "ActiveState": "inactive", "Restart": "no"},
    })
    try:
        assert sg.check_resilience().status == sg.FAIL
    finally:
        sg._systemctl_prop = orig


def test_live_system_is_safe():
    # El sistema VIVO debe estar SAFE (la cura está desplegada). Si esto falla, algo regresó.
    report = sg.audit(skip_network=True)
    fails = [c for c in report["invariants"] if c["status"] == sg.FAIL]
    assert not fails, f"invariantes FALLando en vivo: {[c['id'] for c in fails]}"


if __name__ == "__main__":
    import sys
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed = []
    for t in tests:
        try:
            t()
            passed += 1
            print(f"PASS {t.__name__}")
        except Exception as exc:
            failed.append(t.__name__)
            print(f"FAIL {t.__name__}: {exc}")
            traceback.print_exc()
    print(f"\n== {passed} PASS / {len(failed)} FAIL ==")
    sys.exit(1 if failed else 0)
