"""Contrato del lanzador AISLADO (usuario alice-v2-lab). JARVIS 3-sep-2026, F4 punto 1."""
import json, re
from pathlib import Path
import pytest

import os
ROOT = Path(os.environ.get("ALICE_V2_ISOLATED_ROOT") or Path(__file__).resolve().parents[4])
L = ROOT / "agents/ALICE/v2_shadow/alice_v2_isolated.sh"
S = ROOT / "agents/ALICE/v2_shadow/settings_alice_v2_isolated.json"
S0 = ROOT / "agents/ALICE/v2_shadow/settings_alice_v2.json"

@pytest.fixture(scope="module")
def script(): return L.read_text()

def test_solo_corre_como_alice_v2_lab(script):
    assert '[ "$(id -un)" = "alice-v2-lab" ] ||' in script and "exit 1" in script

def test_nada_del_home_de_dadito(script):
    assert "/home/dadito" not in script.replace("# ", "").split("set -u")[1], "el asiento aislado no puede depender del home de dadito"

def test_config_dir_y_home_propios(script):
    assert 'export HOME="$R"' in script and 'CLAUDE_CONFIG_DIR="$R/claude-config"' in script and "R=/var/lib/alice-v2-lab" in script

def test_flags_del_contrato(script):
    for f in ("--permission-mode dontAsk", "--strict-mcp-config", "--settings", "--append-system-prompt", '--name "ALICE v2 aislada'):
        assert f in script, f
    assert "--dangerously-skip-permissions" not in script

def test_mcp_sin_sudo_en_el_asiento_aislado():
    # el MCP instalado en el arbol del usuario no usa sudo: ya es el usuario
    m = json.loads((ROOT / "agents/ALICE/v2_shadow/mcp_alice_v2_isolated.json").read_text())
    s = m["mcpServers"]["soul-v2-gateway"]; assert s["command"] == "/usr/bin/python3" and "sudo" not in json.dumps(s)

def test_politica_aislada_solo_cambia_la_ruta_del_publicador():
    a, b = json.loads(S.read_text()), json.loads(S0.read_text())
    pa, pb = a["permissions"], b["permissions"]
    isolated_secret_denies = {
        "Read(//var/lib/alice-v2-lab/claude-config/**)",
        "Edit(//var/lib/alice-v2-lab/claude-config/**)",
        "Read(//var/lib/alice-v2-lab/seat/messages/.agent_session_token_*)",
        "Edit(//var/lib/alice-v2-lab/seat/messages/.agent_session_token_*)",
    }
    # 4-sep: el asiento aislado arma sus propios oidos. Medido como alice-v2-lab con
    # `claude -p --permission-mode dontAsk`: deny Bash(tail:*) + allow exacto -> DENEGADO;
    # sin el deny y con el allow exacto -> PERMITIDO; otro comando tail -> DENEGADO igual.
    monitor_cmd = ("flock -n /tmp/seal_monitor_connect_ALICE-V2.lock "
                   "tail -n 0 -F /tmp/seal_events_ALICE-V2.log")
    isolated_monitor_deny_lifted = {"Bash(tail:*)"}
    isolated_monitor_allow = {f"Bash({monitor_cmd})"}
    assert set(pa["deny"]) == (set(pb["deny"]) | isolated_secret_denies) - isolated_monitor_deny_lifted
    diff = (set(pa["allow"]) ^ set(pb["allow"])) - isolated_monitor_allow
    assert diff == {"Bash(/var/lib/alice-v2-lab/seat/scripts/seal_send.py ALICE-V2:*)", "Bash(/home/dadito/IA/proyecto-seal/scripts/seal_send.py ALICE-V2:*)"}, diff

def test_politica_aislada_oculta_credenciales_propias():
    policy = json.loads(S.read_text())["permissions"]
    required = {
        "Read(//var/lib/alice-v2-lab/claude-config/**)",
        "Edit(//var/lib/alice-v2-lab/claude-config/**)",
        "Read(//var/lib/alice-v2-lab/seat/messages/.agent_session_token_*)",
        "Edit(//var/lib/alice-v2-lab/seat/messages/.agent_session_token_*)",
    }
    assert required <= set(policy["deny"])

def test_control_pytest_disponible():
    assert callable(pytest.skip)

def test_credencial_oauth_propia_denegada_por_nombre_oculto():
    """ADA 19:08: el asiento intento leer ~/claude-config/.credentials.json; Read(**/credentials*) no cubre el punto inicial."""
    for p in (S, S0):
        d = set(json.loads(p.read_text())["permissions"]["deny"])
        for n in ("Read(**/.credentials*)", "Read(**/claude-config/**)", "Grep(**/claude-config/**)", "Glob(**/claude-config/**)"):
            assert n in d, (p.name, n)

def test_toda_regla_de_ruta_absoluta_usa_doble_barra():
    """Medido 19:14: Read(/var/lib/.../claude-config/**) con UNA barra NO denego (relativo al proyecto); Read(//var/lib/...) y Read(~/...) SI."""
    for p in (S, S0):
        perm = json.loads(p.read_text())["permissions"]
        for r in perm["allow"] + perm["deny"]:
            m = re.match(r"^(Read|Edit|Write|Grep|Glob)\((/[^/].*)\)$", r)
            assert not m, (p.name, r, "ruta absoluta con una sola barra = inerte")
        assert "Read(~/claude-config/**)" in perm["deny"] and "Read(//var/lib/alice-v2-lab/claude-config/**)" in perm["deny"]
