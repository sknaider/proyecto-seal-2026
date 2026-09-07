"""Contrato del lanzador del asiento «ALICE v2 en sombra» (agents/ALICE/v2_shadow/alice_v2_shadow.sh).

Cada test protege UNA línea de la tabla §2 de F2_asiento_spec.md, con su razón medida. No lanza nada:
lee el script como texto y lo valida con `bash -n`. Los tests son diferenciales a propósito: el arnés
de mutantes (check_alice_v2_shadow_launcher_mutants.py) confirma que cada uno FALLA cuando la línea
que protege se rompe.

El sujeto se localiza por variable de entorno ALICE_V2_LAUNCHER (el arnés apunta a la COPIA mutada) o,
por defecto, al archivo real junto a este directorio de tests.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess

import pytest

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT = HERE.parent / "alice_v2_shadow.sh"


@pytest.fixture(scope="module")
def script() -> str:
    path = pathlib.Path(os.environ.get("ALICE_V2_LAUNCHER") or DEFAULT)
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def script_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("ALICE_V2_LAUNCHER") or DEFAULT)


def _launch_block(script: str) -> str:
    """Desde `seal-claude \\` hasta el mensaje de AUTO-BOOT: la invocación real."""
    m = re.search(r"^seal-claude \\\n(.*?)\n\s*\"\[AUTO-BOOT", script, re.S | re.M)
    assert m, "no encuentro la invocación seal-claude ... [AUTO-BOOT"
    return m.group(1)


def test_sintaxis_bash_valida(script_path):
    rc = subprocess.run(["bash", "-n", str(script_path)], capture_output=True, text=True)
    assert rc.returncode == 0, rc.stderr


def _code_lines(script: str) -> list[str]:
    """Líneas que ejecutan: fuera los comentarios (el encabezado NOMBRA el flag prohibido para explicar por qué no está)."""
    return [l for l in script.splitlines() if not l.lstrip().startswith("#")]


def test_sin_dangerously_skip_permissions(script):
    # Es la capa que v1 no tiene (F2 §3.1). Con el flag, el asiento es «una segunda ALICE, no una sombra».
    assert not any("--dangerously-skip-permissions" in l for l in _code_lines(script))
    assert "--dangerously-skip-permissions" not in _launch_block(script)


def test_permisos_por_settings_y_modo_default(script):
    block = _launch_block(script)
    assert "--permission-mode dontAsk" in block, "lista cerrada sin humano: lo no permitido se DENIEGA solo (3-sep: un ps colgo el examen esperando a JARVIS)"
    assert re.search(r'--settings "\$SPEC_DIR/settings_alice_v2\.json"', block)


def test_mcp_estricto_solo_del_asiento(script):
    # Sin --strict-mcp-config el CLI carga también .mcp.json del repo (postgres, github, web-soul…).
    block = _launch_block(script)
    assert "--strict-mcp-config" in block
    assert re.search(r'--mcp-config "\$SPEC_DIR/mcp_alice_v2\.json"', block)


def test_nombre_no_activa_el_singleton_guard_de_v1(script):
    # alice.sh:83 mata `claude.*--name ALICE` (prefijo). El nombre NO puede empezar con ALICE.
    m = re.search(r'--name "([^"]+)"', _launch_block(script))
    assert m, "sin --name"
    # Lo que ve `ps` es el nombre EXPANDIDO: la variable $ALICE_V2_MODEL no existe en la cmdline, existe su valor.
    name = m.group(1).replace("$ALICE_V2_MODEL", "claude-opus-5").replace("${ALICE_V2_MODEL}", "claude-opus-5")
    assert "$" not in name, f"variable sin expandir en el nombre: {name!r}"
    assert "alice" in name.lower(), "el nombre debe seguir identificando a Alice para un humano"
    # NEXUS (revisión 00:13): alice.sh:83 tiene TRES patrones case-sensitive, y el tercero lleva `.*` entre medio:
    #   claude.*--name ALICE | openclaude.*--name ALICE | node.*--name.*ALICE
    # Si el CLI apareciera como `node` (nvm, npx), cualquier "ALICE" en mayúsculas después de --name mataría la sombra.
    assert "ALICE" not in name, f"--name {name!r} contiene ALICE en mayúsculas: v1 lo mataría al reiniciar"
    guard = re.compile(r"claude.*--name ALICE|openclaude.*--name ALICE|node.*--name.*ALICE")
    for binary in ("/home/dadito/.local/bin/claude", "/usr/bin/node /home/dadito/.nvm/claude/cli.js", "openclaude"):
        simulated_cmdline = f"{binary} --settings x.json --name {name} --model claude-opus-5"
        assert not guard.search(simulated_cmdline), f"el guard de v1 matchea: {simulated_cmdline}"
    # Control de que el guard simulado sí muerde lo que debe: el nombre de v1 matchea en las tres formas.
    for binary in ("/home/dadito/.local/bin/claude", "/usr/bin/node cli.js", "openclaude"):
        assert guard.search(f"{binary} --name ALICE — Team SEAL [Opus5]")


def test_identidad_de_instancia_no_la_de_v1(script):
    assert re.search(r"^export SEAL_AGENT=ALICE-V2$", script, re.M)
    assert not re.search(r"^export SEAL_AGENT=ALICE$", script, re.M)
    assert "seal_identity_env.sh" in script
    # Sin token de instancia no arranca: nunca como ALICE v1.
    assert re.search(r'\[ -n "\$\{SEAL_SESSION_TOKEN:-\}" \] \|\| \{ .*exit 1', script)


def test_estado_propio_del_cli(script):
    # Medido 3-sep 00:01: CLAUDE_CONFIG_DIR se honra (estado y login propios).
    assert re.search(r'^export CLAUDE_CONFIG_DIR="\$SEAL_V2_CONFIG"$', script, re.M)
    assert "proyecto-seal-alice-v2" in script  # worktree propio, no el repo vivo


def test_salida_solo_al_canal_de_sombra(script):
    assert 'SHADOW_CHANNEL="shadow:alice-v2"' in script
    # Toda invocación de seal_send.py en el script va con el canal de sombra.
    joined = script.replace("\\\n", " ")  # la invocación puede continuar en la línea siguiente con `\`
    for m in re.finditer(r"seal_send\.py ALICE-V2 [^\n]*", joined):
        stmt = m.group(0)
        assert ('--channel "$SHADOW_CHANNEL"' in stmt) or ("--channel shadow:alice-v2" in stmt), \
            f"envío sin canal de sombra: {stmt.strip()[:80]}"
    assert "--channel web_chat" not in script
    assert "--channel shadow:alice-v2" in script  # en el prompt del asiento


def test_rutas_tmp_calificadas_y_no_pisa_v1(script):
    assert "/tmp/alice_v2_chat_catchup.json" in script
    assert "/tmp/alice_chat_catchup.json" not in script
    assert "MODO CONSULTA" in script and "webchat_poll" in script  # 3-sep: sin monitor, lectura a demanda
    assert "/tmp/ALICE_V2_monitor_id" not in script and "/tmp/ALICE_monitor_id" not in script
    assert "tmux -L seal-alice-v2" in script
    assert "tmux -L seal-alice " not in script and 'tmux -L seal-alice kill' not in script
    assert "session_checkpoint.py --agent ALICE-V2" in script
    assert "--agent ALICE --read" not in script
    assert "end_session.sh ALICE" not in script


def test_no_mata_procesos_de_v1(script):
    # alice_fresh.sh mata tails con SEAL_AGENT=ALICE y MCP huérfanos; v2 sólo puede tocar lo suyo.
    assert 'grep -qx "SEAL_AGENT=ALICE"' not in script
    assert "mcp_server.py" not in script
    assert re.search(r'grep -qx "SEAL_AGENT=ALICE-V2" && kill', script)


def test_cerebro_por_defecto_es_opus_5(script):
    # William 2-sep 23:41: una sola variable (la arquitectura).
    assert re.search(r'ALICE_V2_MODEL="\$\{ALICE_V2_MODEL:-claude-opus-5\}"', script)
    assert '--model "$ALICE_V2_MODEL"' in script


def test_prompt_declara_sombra_y_no_general(script):
    assert "boot_context(agent=\"ALICE\")" in script  # misma alma
    assert "vos NO publicas en web_chat ni en DMs" in script
    assert "NO la rodees" in script
