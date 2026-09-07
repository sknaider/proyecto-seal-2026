"""Los tres switches del coordinador deben fallar CERRADO.

Generador: `messages/chat_server.py` leia un archivo de control de 8 bytes y
devolvia "OFF" cuando faltaba.  Borrarlo apagaba la autenticacion de agentes sin
reiniciar el daemon.  FABLE lo listo el 31-ago-2026 y otra vez el 2-sep sin que
nadie lo cerrara; no habia UN test que cubriera estas funciones.

Cada caso trae su par: el ROJO (lo que no debe pasar) y el VERDE (la capacidad
que no queremos romper).  Un test que solo afirmara "ausente -> ENFORCE" pasaria
igual si la funcion devolviera ENFORCE SIEMPRE, y ahi perderiamos el rollback.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHAT_SERVER = ROOT / "messages" / "chat_server.py"


def _load_rollout_mode():
    """Trae solo `_rollout_mode` sin ejecutar el modulo (importa FastAPI y DB)."""
    import ast

    tree = ast.parse(CHAT_SERVER.read_text(encoding="utf-8"))
    wanted = {"_rollout_mode", "_ROLLOUT_MODES"}
    picked = [
        node
        for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in wanted)
        or (
            isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) in wanted for t in node.targets)
        )
    ]
    assert len(picked) == 2, f"esperaba helper + constante, encontre {len(picked)}"
    ns = {"os": __import__("os"), "Path": Path}
    exec(compile(ast.Module(body=picked, type_ignores=[]), "<rollout>", "exec"), ns)
    return ns["_rollout_mode"]


rollout_mode = _load_rollout_mode()

FILE_ENV = "SEAL_TEST_MODE_FILE"
VALUE_ENV = "SEAL_TEST_MODE_VALUE"


def _call(monkeypatch, *, file_contents=None, env_value=None, tmp_path=None):
    if file_contents is None:
        target = (tmp_path or Path("/nonexistent")) / "no_existe_este_archivo"
    else:
        target = tmp_path / "mode"
        target.write_text(file_contents, encoding="utf-8")
    monkeypatch.setenv(FILE_ENV, str(target))
    if env_value is None:
        monkeypatch.delenv(VALUE_ENV, raising=False)
    else:
        monkeypatch.setenv(VALUE_ENV, env_value)
    return rollout_mode(FILE_ENV, ".config/seal/irrelevante", VALUE_ENV)


# ---------------------------------------------------------------- ROJO
def test_archivo_ausente_no_apaga_el_control(monkeypatch, tmp_path):
    """El defecto exacto: `rm` del archivo devolvia OFF."""
    assert _call(monkeypatch, tmp_path=tmp_path) == "ENFORCE"


def test_valor_invalido_no_apaga_el_control(monkeypatch, tmp_path):
    """Un typo ('ENFOCE') no debe abrir la puerta."""
    assert _call(monkeypatch, file_contents="ENFOCE", tmp_path=tmp_path) == "ENFORCE"


def test_archivo_vacio_no_apaga_el_control(monkeypatch, tmp_path):
    assert _call(monkeypatch, file_contents="   \n", tmp_path=tmp_path) == "ENFORCE"


def test_env_invalido_con_archivo_ausente(monkeypatch, tmp_path):
    assert _call(monkeypatch, env_value="basura", tmp_path=tmp_path) == "ENFORCE"


# ---------------------------------------------------------------- VERDE
# Sin estos, un `return "ENFORCE"` constante pasaria los de arriba.
@pytest.mark.parametrize("declarado", ["OFF", "SHADOW", "ENFORCE"])
def test_el_archivo_sigue_mandando(monkeypatch, tmp_path, declarado):
    assert _call(monkeypatch, file_contents=declarado, tmp_path=tmp_path) == declarado


def test_apagar_explicito_sigue_siendo_posible(monkeypatch, tmp_path):
    """Rollback: se apaga ESCRIBIENDO 'OFF', no borrando el archivo."""
    assert _call(monkeypatch, file_contents="off", tmp_path=tmp_path) == "OFF"


def test_env_var_sigue_sirviendo_de_rollback(monkeypatch, tmp_path):
    assert _call(monkeypatch, env_value="OFF", tmp_path=tmp_path) == "OFF"
