"""El detector de credenciales fuera del respaldo.

Cada brazo fija un modo de falla REAL, no una funcion. El caso 0 es el que le
dio origen: yo cree un EnvironmentFile y no lo agregue a la lista.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import seal_credenciales_sin_respaldo as det  # noqa: E402


def _monta(tmp_path, en_disco=(), declaradas=()):
    raiz = tmp_path / "seal"
    (raiz / "env").mkdir(parents=True)
    for n in en_disco:
        p = raiz / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("X=1")
    lista = tmp_path / "lista.txt"
    lista.write_text("\n".join(str(raiz / d) for d in declaradas) + "\n")
    return raiz, lista


def test_el_caso_que_lo_origino_una_credencial_nueva_sin_declarar(tmp_path):
    raiz, lista = _monta(tmp_path, ["env/nuevo.env", "env/viejo.env"], ["env/viejo.env"])
    assert det.huerfanas(raiz, lista) == [str(raiz / "env/nuevo.env")]


def test_CONTROL_todo_declarado_no_reporta_nada(tmp_path):
    """Sin esto, un detector que siempre reporta vacio pasaria igual."""
    raiz, lista = _monta(tmp_path, ["env/a.env"], ["env/a.env"])
    assert det.huerfanas(raiz, lista) == []


def test_CONTROL_declarar_de_mas_no_inventa_huerfanas(tmp_path):
    raiz, lista = _monta(tmp_path, [], ["env/no_existe.env"])
    assert det.huerfanas(raiz, lista) == []


def test_cubre_dsn_key_y_cred_no_solo_env(tmp_path):
    raiz, lista = _monta(tmp_path, ["a.dsn", "b.key", "c.cred", "d.env"], [])
    assert len(det.huerfanas(raiz, lista)) == 4


def test_ignora_respaldos_y_ejemplos(tmp_path):
    raiz, lista = _monta(tmp_path, ["a.env.bak", "b.env.example", "c.INCORRECTO.env"], [])
    assert det.huerfanas(raiz, lista) == []


def test_encuentra_en_subdirectorios(tmp_path):
    raiz, lista = _monta(tmp_path, ["mcp_agents/x.dsn"], [])
    assert det.huerfanas(raiz, lista) == [str(raiz / "mcp_agents/x.dsn")]


def test_una_lista_AUSENTE_no_puede_dar_verde(tmp_path, monkeypatch, capsys):
    """El modo de falla del detector diferencial: sin lista, cero diferencias.

    Si la lista desaparece, `en_disco - vacio` daria TODAS como huerfanas, pero
    peor seria el reves: un detector que lee vacio y calla. Se falla cerrado con
    codigo 2 y un error explicito.
    """
    raiz, _ = _monta(tmp_path, ["a.env"], [])
    monkeypatch.setattr(sys, "argv", ["x", "--raiz", str(raiz), "--lista", str(tmp_path / "no_existe.txt")])
    assert det.main() == 2
    assert "lista_ausente" in capsys.readouterr().out


def test_el_codigo_de_salida_distingue_los_tres_casos(tmp_path, monkeypatch):
    raiz, lista = _monta(tmp_path, ["a.env"], [])
    monkeypatch.setattr(sys, "argv", ["x", "--raiz", str(raiz), "--lista", str(lista)])
    assert det.main() == 1, "con huerfanas debe salir 1 para servir de freno"
    raiz2, lista2 = _monta(tmp_path / "b", ["a.env"], ["a.env"])
    monkeypatch.setattr(sys, "argv", ["x", "--raiz", str(raiz2), "--lista", str(lista2)])
    assert det.main() == 0
