from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


HOOK = Path(__file__).with_name("pre_tool_hook.py")


def _run_hook(event: dict, *, token: str = "synthetic-token-never-persist") -> dict:
    env = dict(os.environ)
    env["SEAL_AGENT"] = "ADA"
    env["SEAL_SESSION_TOKEN"] = token
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert token not in proc.stdout
    return json.loads(proc.stdout)


def test_mcp_tool_does_not_copy_bearer_secret_into_tool_input() -> None:
    result = _run_hook(
        {
            "tool_name": "mcp__seal-memory__memory_search",
            "tool_input": {"query": "estado"},
        }
    )
    assert result == {}


def test_memory_store_enrichment_preserves_payload_without_session_token() -> None:
    result = _run_hook(
        {
            "tool_name": "mcp__seal-memory__memory_store",
            "tool_input": {"content": "hito", "category": "operational"},
        }
    )
    updated = result["hookSpecificOutput"]["updatedInput"]
    assert updated["agent"] == "ADA"
    assert updated["content"] == "hito"
    assert "timestamp" in updated
    assert "session_token" not in updated


def test_unquoted_heredoc_is_blocked_before_shell_can_expand_text() -> None:
    result = _run_hook(
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": "python3 - <<PY\nprint('control `0600`')\nPY",
            },
        }
    )
    decision = result["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "Unquoted heredoc" in decision["permissionDecisionReason"]


def test_quoted_heredoc_and_stdin_style_remain_available() -> None:
    quoted = _run_hook(
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": "python3 - <<'PY'\nprint('control `0600`')\nPY",
            },
        }
    )
    stdin_style = _run_hook(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "python3 safe_writer.py --stdin"},
        }
    )
    assert quoted == {}
    assert stdin_style == {}


def test_heredoc_sin_comillas_con_dolar_parentesis_tambien_se_bloquea() -> None:
    """Hueco hallado MUTANDO mi propio fix (NEXUS, 8-sep-2026).

    El mutante H3 —que la guarda mire solo el acento grave y NO `$( )`—
    SOBREVIVIA a los 93 brazos. Y las dos formas son la misma cosa para el
    shell: sustitucion de comandos que se EJECUTA al expandir el cuerpo del
    heredoc sin comillas.

    Cubrir una de las dos y creer que se cubrio la clase es exactamente el
    error del que vengo tirando el hilo todo el dia.
    """
    result = _run_hook(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "cat <<EOF\n  hoy es $(date)\nEOF"},
        }
    )
    decision = result["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "Unquoted heredoc" in decision["permissionDecisionReason"]


def test_heredoc_sin_comillas_y_SIN_sustitucion_sigue_permitido() -> None:
    """El control que impide que el fix se vuelva un bloqueo general.

    En el repo hay 11 scripts con heredoc sin comillas que lo usan de forma
    legitima. Denegarlos todos habria sido cambiar un agujero por un freno.
    """
    result = _run_hook(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "cat <<EOF\n  una linea de texto comun\nEOF"},
        }
    )
    assert result == {}, "un heredoc sin sustitucion no debe bloquearse"


# ───── huecos que hallo FABLE rechazando mi primera version (8-sep-2026) ─────
#
# Mi v1 buscaba aperturas con `finditer` sobre TODO el comando. FABLE mostro que
# eso encuentra tambien las aperturas MENCIONADAS dentro del cuerpo de un heredoc
# citado -texto inerte- y bloqueaba de mas. Ademas sobrevivian cuatro mutantes:
# mirar solo el primer heredoc, tomar el cuerpo hasta el final siempre, tratar
# cualquier `$` como sustitucion, y tratar las comillas DOBLES como si no
# hubiera comillas.
#
# La v2 es un barrido que CONSUME cada cuerpo. Estos brazos fijan los cinco casos.

def _hd():
    from importlib.util import module_from_spec, spec_from_file_location
    sp = spec_from_file_location("hook_hd", str(HOOK))
    m = module_from_spec(sp); sp.loader.exec_module(m)
    return m.heredoc_sin_comillas_con_sustitucion


GRAVE = chr(96)


def test_qa_negative_una_apertura_MENCIONADA_dentro_de_un_heredoc_citado_no_cuenta():
    """El falso positivo de FABLE. Un heredoc citado cuyo cuerpo habla DE otro
    heredoc es documentacion, no codigo: nada de eso se ejecuta."""
    cmd = ("python3 - <<'PYEOF'\nprint('ej: cat <<EOF con " + GRAVE + "date" + GRAVE +
           "')\nPYEOF")
    assert _hd()(cmd) is None


def test_qa_negative_el_delimitador_con_comillas_DOBLES_tambien_es_inerte():
    """`<<"EOF"` no expande, igual que `<<'EOF'`. Sin este brazo, un mutante que
    trate las dobles como si no hubiera comillas SOBREVIVE."""
    cmd = 'cat <<"EOF"\nhoy $(date)\nEOF'
    assert _hd()(cmd) is None


def test_el_SEGUNDO_heredoc_tambien_se_mira_no_solo_el_primero():
    """Mutante de FABLE: quedarse con la primera apertura. El peligro puede
    estar en la segunda."""
    cmd = "cat <<A\ntexto plano\nA\ncat <<B\nhoy $(date)\nB"
    assert _hd()(cmd) == "B"


def test_un_heredoc_SIN_CIERRE_se_mira_hasta_el_final():
    """Si el cuerpo se cortara en el primer salto de linea, bastaria con no
    cerrar el heredoc para colar la sustitucion."""
    assert _hd()("cat <<EOF\nhoy $(date)") == "EOF"


def test_qa_control_un_DOLAR_a_secas_no_es_sustitucion():
    """`$VAR` expande pero NO ejecuta. Tratar cualquier `$` como peligro
    convierte la guarda en un freno: es la diferencia entre proteger y estorbar."""
    assert _hd()("cat <<EOF\nsolo $VAR sin ejecutar\nEOF") is None


def test_qa_control_una_comilla_sin_cerrar_no_concluye_nada():
    """Entrada rara: la guarda se abstiene en vez de inventar un veredicto."""
    assert _hd()("echo 'una comilla sin cerrar") is None
