#!/usr/bin/env python3
"""Mutantes adversariales del lanzador alice_v2_shadow.sh: cada uno rompe UNA línea del contrato
(F2 §2) sobre una COPIA en un tmp fresco; el test debe FALLAR (rc==1). rc∉{0,1} = sospechoso, no muerto.
Control positivo: la copia sin mutar pasa. Control negativo del oráculo: una copia con sintaxis rota
debe salir como sospechosa (bash -n falla → pytest rc 1 sí, pero lo listamos aparte para leerlo).
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SUBJECT = ROOT / "alice_v2_shadow.sh"
TEST = ROOT / "tests" / "test_alice_v2_shadow_launcher_v1.py"
POLICY_TEST = ROOT / "tests" / "test_alice_v2_shadow_policy_v1.py"
SETTINGS = ROOT / "settings_alice_v2.json"
MCP = ROOT / "mcp_alice_v2.json"

# Mutantes de POLÍTICA (NEXUS, revisión 00:13: «alguien afloja un deny mañana y el gate sigue verde»).
# Cada uno es una transformación del JSON; el killer es test_alice_v2_shadow_policy_v1.py sobre la copia.
def _m_quita_deny_python(d): d["permissions"]["deny"].remove("Bash(python3:*)"); return d
def _m_allow_webfetch(d): d["permissions"]["allow"].append("WebFetch"); return d
def _m_allow_cat(d): d["permissions"]["allow"].append("Bash(cat:*)"); return d
def _m_quita_deny_write_repo(d): d["permissions"]["deny"].remove("Write(//home/dadito/IA/proyecto-seal/**)"); return d
def _m_allow_bash_total(d): d["permissions"]["allow"].append("Bash"); return d
def _m_allow_perl(d): d["permissions"]["allow"].append("Bash(perl:*)"); return d   # NEXUS 00:18: asimetría allow/deny
def _m_modo_bypass(d): d["permissions"]["defaultMode"] = "bypassPermissions"; return d
def _m_quita_deny_memory_store(d): d["permissions"]["deny"].remove("mcp__soul-v2-gateway__memory_store"); return d
def _m_publica_como_v1(d): d["permissions"]["allow"].append("Bash(/home/dadito/IA/proyecto-seal/scripts/seal_send.py ALICE:*)"); return d
def _m_mcp_agrega_postgres(d): d["mcpServers"]["postgres"] = {"command": "python3", "args": ["x.py"]}; return d
def _m_mcp_runs_as_dadito(d): d["mcpServers"]["soul-v2-gateway"]["args"][2] = "dadito"; return d
def _m_mcp_direct_http(d):
    d["mcpServers"] = {"seal-memory": {"type": "http", "url": "http://127.0.0.1:8771/mcp", "headers": {"Authorization": "Bearer ${SEAL_MCP_TOKEN_ALICE_V2}"}}}
    return d
def _m_git_diff_vuelve_al_allow(d):
    d["permissions"]["allow"].append("Bash(git diff:*)"); d["permissions"]["deny"].remove("Bash(git diff:*)"); return d

def _m_quita_allow(d, tool):
    d["permissions"]["allow"].remove(tool)
    return d
POLICY_MUTANTS = {
    "settings_quita_deny_python": (SETTINGS, "ALICE_V2_SETTINGS", _m_quita_deny_python),
    "settings_allow_webfetch": (SETTINGS, "ALICE_V2_SETTINGS", _m_allow_webfetch),
    "settings_allow_cat": (SETTINGS, "ALICE_V2_SETTINGS", _m_allow_cat),
    "settings_quita_deny_write_repo": (SETTINGS, "ALICE_V2_SETTINGS", _m_quita_deny_write_repo),
    "settings_allow_bash_total": (SETTINGS, "ALICE_V2_SETTINGS", _m_allow_bash_total),
    "settings_allow_perl": (SETTINGS, "ALICE_V2_SETTINGS", _m_allow_perl),
    "settings_modo_bypass": (SETTINGS, "ALICE_V2_SETTINGS", _m_modo_bypass),
    "settings_quita_deny_memory_store": (SETTINGS, "ALICE_V2_SETTINGS", _m_quita_deny_memory_store),
    "settings_git_diff_vuelve_al_allow": (SETTINGS, "ALICE_V2_SETTINGS", _m_git_diff_vuelve_al_allow),
    "settings_publica_como_v1": (SETTINGS, "ALICE_V2_SETTINGS", _m_publica_como_v1),
    "mcp_agrega_postgres": (MCP, "ALICE_V2_MCP", _m_mcp_agrega_postgres),
    "mcp_runs_as_dadito": (MCP, "ALICE_V2_MCP", _m_mcp_runs_as_dadito),
    "mcp_direct_http": (MCP, "ALICE_V2_MCP", _m_mcp_direct_http),
}
for _tool in (
    "boot_context", "active_recall", "memory_hybrid_search", "search_code",
    "soul_recall_router_tool", "soul_snapshot", "webchat_poll", "working_state_get",
):
    _entry = f"mcp__soul-v2-gateway__{_tool}"
    POLICY_MUTANTS[f"settings_quita_allow_{_tool}"] = (
        SETTINGS,
        "ALICE_V2_SETTINGS",
        lambda d, entry=_entry: _m_quita_allow(d, entry),
    )


def run_policy_test(env_name: str, copy: pathlib.Path) -> int:
    env = {**os.environ, env_name: str(copy), "PYTHONDONTWRITEBYTECODE": "1"}
    rc = subprocess.run([PY, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", str(POLICY_TEST)],
                        capture_output=True, text=True, env=env, timeout=120)
    return rc.returncode
PY = os.environ.get("SEAL_QUALITY_PYTHON", sys.executable)

MUTANTS = {
    "permission_mode_pide_humano": ('  --permission-mode dontAsk \\', '  --permission-mode default \\'),
    "skip_permissions_agregado": ('  --permission-mode dontAsk \\', '  --dangerously-skip-permissions \\'),
    "sin_strict_mcp": ('  --strict-mcp-config --mcp-config "$SPEC_DIR/mcp_alice_v2.json" \\', '  --mcp-config "$SPEC_DIR/mcp_alice_v2.json" \\'),
    "nombre_con_ALICE_mayusculas": ('--name "Sombra v2 de Alice [$ALICE_V2_MODEL]"', '--name "Sombra v2 ALICE [$ALICE_V2_MODEL]"'),
    "identidad_de_v1": ('export SEAL_AGENT=ALICE-V2', 'export SEAL_AGENT=ALICE'),
    "sin_config_dir_propio": ('export CLAUDE_CONFIG_DIR="$SEAL_V2_CONFIG"', '# export CLAUDE_CONFIG_DIR="$SEAL_V2_CONFIG"'),
    "canal_general": ('SHADOW_CHANNEL="shadow:alice-v2"', 'SHADOW_CHANNEL="web_chat"'),
    "catchup_de_v1": ('CATCHUP_FILE="/tmp/alice_v2_chat_catchup.json"', 'CATCHUP_FILE="/tmp/alice_chat_catchup.json"'),
    "mata_tails_de_v1": ('grep -qx "SEAL_AGENT=ALICE-V2" && kill', 'grep -qx "SEAL_AGENT=ALICE" && kill'),
    "cerebro_gemma": ('ALICE_V2_MODEL="${ALICE_V2_MODEL:-claude-opus-5}"', 'ALICE_V2_MODEL="${ALICE_V2_MODEL:-gemma4-26b-local}"'),
    "sin_guard_de_token": ('[ -n "${SEAL_SESSION_TOKEN:-}" ] || { echo "FATAL: sin token de instancia ALICE-V2; no arranco como ALICE" >&2; exit 1; }', ': token opcional'),
}


def run_test(copy: pathlib.Path) -> int:
    env = {**os.environ, "ALICE_V2_LAUNCHER": str(copy), "PYTHONDONTWRITEBYTECODE": "1"}
    rc = subprocess.run([PY, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", str(TEST)],
                        capture_output=True, text=True, env=env, timeout=120)
    return rc.returncode


def main() -> int:
    src = SUBJECT.read_text(encoding="utf-8")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="alice-v2-mutants-"))
    detail, killed, survived, suspicious = {}, 0, 0, 0
    unmut = tmp / "unmutated.sh"; unmut.write_text(src, encoding="utf-8")
    pos_rc = run_test(unmut)
    broken = tmp / "broken.sh"; broken.write_text(src + "\nif [ ; then\n", encoding="utf-8")
    neg_rc = run_test(broken)
    for name, (old, new) in MUTANTS.items():
        assert src.count(old) >= 1, f"mutante {name}: patrón no encontrado en el sujeto"
        mut = tmp / f"{name}.sh"; mut.write_text(src.replace(old, new, 1), encoding="utf-8")
        rc = run_test(mut)
        state = "killed" if rc == 1 else ("survived" if rc == 0 else "suspicious")
        detail[name] = {"rc": rc, "state": state}
        killed += state == "killed"; survived += state == "survived"; suspicious += state == "suspicious"
    # Política: control positivo (copias sin mutar) y mutantes JSON.
    pos_settings = tmp / "settings_unmutated.json"; pos_settings.write_text(SETTINGS.read_text(encoding="utf-8"), encoding="utf-8")
    pos_mcp = tmp / "mcp_unmutated.json"; pos_mcp.write_text(MCP.read_text(encoding="utf-8"), encoding="utf-8")
    pos_policy_rc = max(run_policy_test("ALICE_V2_SETTINGS", pos_settings), run_policy_test("ALICE_V2_MCP", pos_mcp))
    for name, (src_file, env_name, mutate) in POLICY_MUTANTS.items():
        d = json.loads(src_file.read_text(encoding="utf-8"))
        mut = tmp / f"{name}.json"; mut.write_text(json.dumps(mutate(d)), encoding="utf-8")
        rc = run_policy_test(env_name, mut)
        state = "killed" if rc == 1 else ("survived" if rc == 0 else "suspicious")
        detail[name] = {"rc": rc, "state": state}
        killed += state == "killed"; survived += state == "survived"; suspicious += state == "suspicious"
    total = len(MUTANTS) + len(POLICY_MUTANTS)
    out = {
        "schema": "seal.mutation-evidence.v1",
        "tool": "explicit adversarial mutants on a COPY of agents/ALICE/v2_shadow/alice_v2_shadow.sh per mutant in a fresh tmp dir; killer = test rc==1 in subprocess pytest; rc not in (0,1) = suspicious",
        "workspace": "asiento ALICE v2 en sombra: lanzador + política cerrada + gateway SOUL v2 por UID aislado, sin bearer visible",
        "killed": killed, "survived": survived, "no_tests": 0, "skipped": 0, "suspicious": suspicious, "timeout": 0,
        "total": total, "mutation_score_percent": round(100.0 * killed / total, 1),
        "positive_control_unmutated_copy": {"rc": pos_rc, "ok": pos_rc == 0},
        "positive_control_unmutated_policy": {"rc": pos_policy_rc, "ok": pos_policy_rc == 0},
        "oracle_negative_control_broken_syntax": {"rc": neg_rc, "classified_as_killed": neg_rc == 1, "note": "bash -n falla → el test de sintaxis lo mata; se lista aparte para que se lea como control, no como mutante"},
        "reviewer": "pendiente",
        # Sellos de TODOS los sujetos y tests del manifiesto (lección 2-sep: la evidencia debe cubrirlos todos).
        "file_sha256": {str(f.relative_to(ROOT.parents[2])): hashlib.sha256(f.read_bytes()).hexdigest() for f in (
            SUBJECT, ROOT / "settings_alice_v2.json", ROOT / "mcp_alice_v2.json", pathlib.Path(__file__).resolve(),
            TEST, ROOT / "tests" / "test_alice_v2_shadow_launcher_control_v1.py", POLICY_TEST)},
        "detail": detail, "tmp_dir": str(tmp),
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if (pos_rc == 0 and pos_policy_rc == 0 and survived == 0 and suspicious == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
