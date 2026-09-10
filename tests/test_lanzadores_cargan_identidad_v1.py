#!/usr/bin/env python3
"""Los lanzadores de agente exportan una identidad que el MCP RECONOCE (JARVIS, 10-sep-2026).

Que lo genero, medido: toda herramienta privada del MCP respondia
``[TOOL_BROKER] ... blocked for external`` para JARVIS, ALICE y NEXUS. La causa no era
el servidor ni los permisos: ``.mcp.json`` manda ``Authorization: Bearer
${SEAL_SESSION_TOKEN}`` y esa variable no estaba poblada, asi que el bearer no resolvia
a nadie y ``_get_caller_agent()`` caia a ``"external"`` en modo ENFORCE.

**El arreglo ya existia y no estaba cableado**: ``seal_identity_env.sh`` hacia exactamente
esto, y lo cargaban ``ada_codex.sh``, ``fable_juez.sh`` y ``alice_v2_shadow.sh`` -- pero no
los lanzadores de los tres agentes que quedaban en ``external``.

**Por que estos brazos y no un grep:** verificar que el archivo CONTIENE la linea prueba
que esta escrita, no que funciona. El brazo que carga peso es el de EFECTO: se corre el
helper en un entorno limpio y se le pregunta a ``token_owner`` -- la misma funcion que usa
el servidor -- por quien es el token resultante. Y el que lo hace no vacuo es el CONTROL
NEGATIVO: un token inventado tiene que resolver a ``None``, porque si ``token_owner``
dijera que si a cualquier cosa, el brazo positivo pasaria igual sin probar nada.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
HELPER = RAIZ / "seal_identity_env.sh"
LANZADORES = {"JARVIS": RAIZ / "jarvis.sh", "ALICE": RAIZ / "alice.sh", "NEXUS": RAIZ / "nexus.sh"}


def _entorno_limpio(agente: str | None) -> dict[str, str]:
    """Entorno minimo: sin heredar SEAL_* de la sesion que corre los tests.

    Sin esto el brazo mediria la variable que YA tenia el shell padre en vez de la que
    exporta el helper -- un falso verde que ademas cambia segun quien corra la suite.
    """
    env = {
        "HOME": os.environ.get("HOME", "/home/dadito"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    for clave in ("XDG_RUNTIME_DIR", "XDG_CONFIG_HOME"):
        if clave in os.environ:
            env[clave] = os.environ[clave]
    if agente is not None:
        env["SEAL_AGENT"] = agente
    return env


def _token_exportado(agente: str | None) -> tuple[int, str]:
    """Corre el helper en un shell limpio y devuelve (rc DEL SOURCE, token). Nunca lo imprime.

    El rc que interesa es el del ``source``, no el del shell (JARVIS, medido al escribir
    esto): el helper usa ``return 2`` cuando falta ``SEAL_AGENT``, y en un archivo cargado
    con ``source`` eso corta la carga pero **el shell sigue**. Mi primera version leia
    ``p.returncode`` -- el del ``printf`` final, siempre 0 -- y el brazo de fallo cerrado
    daba rojo sobre un helper que se comportaba bien. Medir el rc equivocado acusa a un
    sujeto sano.
    """
    guion = (
        f"source {HELPER} >/dev/null 2>&1; _rc=$?; "
        'printf "%s\\n%s" "$_rc" "${SEAL_SESSION_TOKEN:-}"'
    )
    p = subprocess.run(["bash", "-c", guion], capture_output=True, text=True,
                       env=_entorno_limpio(agente), timeout=60)
    rc_texto, _, token = p.stdout.partition("\n")
    return int(rc_texto.strip() or 1), token.strip()


def _duenio(token: str) -> str | None:
    sys.path.insert(0, str(RAIZ / "memory"))
    from seal_identity_tokens import token_owner  # noqa: E402

    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return token_owner([os.path.join(runtime, "seal"), "/tmp/seal_tokens"],
                       set(LANZADORES) | {"ADA", "FABLE", "DUM"}, token)


@pytest.mark.parametrize("agente", sorted(LANZADORES))
def test_el_lanzador_carga_el_helper_DESPUES_de_fijar_el_agente(agente: str) -> None:
    """El orden importa: el helper aborta si SEAL_AGENT no esta puesto todavia."""
    texto = LANZADORES[agente].read_text(encoding="utf-8")
    m_export = re.search(rf"^export SEAL_AGENT={agente}\b", texto, re.MULTILINE)
    m_source = re.search(r"^source .*seal_identity_env\.sh", texto, re.MULTILINE)
    assert m_export, f"{LANZADORES[agente].name} no fija SEAL_AGENT={agente}"
    assert m_source, (
        f"{LANZADORES[agente].name} no carga seal_identity_env.sh: su sesion quedara "
        "sin SEAL_SESSION_TOKEN y el MCP la vera como 'external'"
    )
    assert m_export.start() < m_source.start(), (
        "el source va DESPUES del export: el helper exige SEAL_AGENT y aborta sin el"
    )


@pytest.mark.parametrize("agente", sorted(LANZADORES))
def test_el_helper_exporta_un_token_que_el_servidor_resuelve_a_ESE_agente(agente: str) -> None:
    """EFECTO: no que la linea este, sino que produzca una identidad reconocible."""
    rc, token = _token_exportado(agente)
    assert rc == 0, f"el helper fallo para {agente}"
    assert token, f"{agente} quedaria sin SEAL_SESSION_TOKEN y el MCP lo veria 'external'"
    assert _duenio(token) == agente, (
        f"el token exportado para {agente} no lo resuelve el servidor a {agente}"
    )


def test_control_un_token_inventado_NO_resuelve_a_nadie() -> None:
    """Sin este control el brazo de arriba pasaria aunque token_owner dijera que si a todo."""
    assert _duenio("0" * 64) is None
    assert _duenio("") is None


def test_sin_SEAL_AGENT_el_helper_falla_CERRADO_y_no_exporta_token() -> None:
    """El olvido tiene que dejar al agente sin identidad, nunca darle una ajena."""
    rc, token = _token_exportado(None)
    assert rc != 0, "sin SEAL_AGENT el helper deberia fallar, no seguir"
    assert token == "", "sin SEAL_AGENT no puede exportarse ningun token"


def test_los_tres_lanzadores_reciben_tokens_DISTINTOS() -> None:
    """Un helper que devolviera el mismo secreto a todos haria intercambiables las identidades."""
    tokens = {a: _token_exportado(a)[1] for a in LANZADORES}
    assert all(tokens.values()), "algun lanzador quedo sin token"
    assert len(set(tokens.values())) == len(tokens), "dos agentes comparten el mismo token"
