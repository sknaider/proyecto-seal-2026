"""Brazos de la voz inmediata y del plazo de relevo.

QUE LO GENERA. William, 9-sep-2026, dos ordenes en la misma conversacion:

    "60 segundos como maximo"
    "sabes que la app de claude responde al momento, quiero igual las respuestas
     de esa manera o un agente designado, me gustaria que sea alice"

El turno unico nacio para que cinco partes tecnicos iguales no lo ahogaran, y ese
problema sigue vivo: por eso NO se apaga el coordinador. Se saca a UN agente de la
cola, igual que ya estaban exentos el saludo y el afecto (regla suya del 31-jul).

EL RIESGO QUE MIDEN ESTOS BRAZOS no es que la voz inmediata no hable: es que
hable de mas. Una regla mal puesta convierte "una voz" en "cinco voces" y le
devuelve a William el flood del que lo sacamos. Por eso hay tantos brazos sobre
lo que NO debe cambiar como sobre lo que si.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import soul_coordination as sc  # noqa: E402


@pytest.fixture()
def bandera(tmp_path):
    def _crear(contenido: str = "ALICE") -> str:
        p = tmp_path / ".voz_inmediata"
        p.write_text(contenido, encoding="utf-8")
        return str(p)
    return _crear


def _publican(mode, lead, flag=None, **kw):
    filas = sc.build_assignments(mode, lead, voz_inmediata_flag=flag, **kw)
    return {f["agent"] for f in filas if f.get("public_write") is True}


# ───────────────── el plazo: la orden de las "60 segundos" ─────────────────

def test_el_plazo_de_relevo_es_60_segundos():
    """William, textual: «60 segundos como maximo». Estaba en 90 desde el 2-sep."""
    assert sc.CASCADE_TURN_TIMEOUT_S == 60.0


def test_el_relevo_pasa_el_turno_al_siguiente_al_vencer_el_plazo():
    """El mecanismo que YA existia y yo afirme en publico que no existia.

    Sin este brazo el numero de arriba es una constante sin lectores — que es
    exactamente lo que le paso a esta misma constante antes (lo dice su propio
    comentario en el modulo).
    """
    cadena = sc.cascade_order()
    ahora = 1_000_000.0
    a_tiempo = sc.build_assignments("cascade", cadena[0], turn_opened_at=ahora, now=ahora + 30)
    vencido  = sc.build_assignments("cascade", cadena[0], turn_opened_at=ahora, now=ahora + 61)
    abierto = lambda filas: next(f["agent"] for f in filas if f.get("public_write"))
    assert abierto(a_tiempo) == cadena[0], "a los 30 s el turno sigue siendo del primero"
    assert abierto(vencido) == cadena[1], "a los 61 s tiene que haber pasado al siguiente"


def test_a_los_59_segundos_TODAVIA_no_releva():
    """Control del borde. Sin el, un mutante que cambie 60 por 1 pasaria igual
    porque el brazo de arriba mide a los 61."""
    cadena = sc.cascade_order()
    filas = sc.build_assignments("cascade", cadena[0], turn_opened_at=0.0, now=59.0)
    assert next(f["agent"] for f in filas if f.get("public_write")) == cadena[0]


# ───────────── la voz inmediata: en TODOS los modos, no solo uno ─────────────

@pytest.mark.parametrize("mode,lead", [
    ("direct", "JARVIS"), ("execution", "JARVIS"), ("discussion", "JARVIS"),
    ("roundtable", "NEXUS"), ("cascade", "JARVIS"), ("social", "JARVIS"),
])
def test_la_voz_inmediata_publica_en_TODOS_los_modos(mode, lead, bandera):
    """El defecto que motivo la envoltura: el relevo existente vivia SOLO dentro
    de `if mode == "cascade"`, y la queja de William cayo en modo `direct`.
    Una regla que cubre un modo de seis se siente puesta y no lo esta."""
    assert "ALICE" in _publican(mode, lead, bandera())


@pytest.mark.parametrize("mode", ["direct", "execution", "discussion", "cascade"])
def test_la_voz_inmediata_NO_le_quita_el_turno_al_lead(mode, bandera):
    """Que ella conteste no debe CALLAR al que le tocaba: son dos voces, no una
    sustitucion. Si se lo quitara, arreglariamos la demora rompiendo el trabajo.

    PARAMETRIZADO POR UNA RAZON MEDIDA, no por prolijidad. La version anterior
    probaba solo `direct`, y el mutante M7 -"al dar la voz inmediata, callar a
    todos los demas"- SOBREVIVIA a los 25 brazos. Motivo: en `direct` ALICE no
    figura en las filas, asi que se toma la rama que la AGREGA; la linea que M7
    muta vive en la rama que VOLTEA una fila existente, y a esa solo se llega en
    `cascade`, donde ella ya es un eslabon de la cadena.

    **El brazo pasaba por el camino equivocado**: verde, y sin tocar el codigo
    que decia cubrir.
    """
    con = _publican(mode, "JARVIS", bandera())
    assert {"JARVIS", "ALICE"} <= con, f"en modo {mode} se perdio una de las dos voces"


def test_los_OTROS_siguen_con_turno_unico(bandera):
    """EL BRAZO QUE MAS IMPORTA. El riesgo no es que ALICE no hable: es que la
    regla abra la puerta a los cinco y le devuelva el flood a William."""
    con = _publican("direct", "JARVIS", bandera())
    assert con == {"JARVIS", "ALICE"}, f"solo el lead y la voz inmediata; salieron {con}"
    assert "NEXUS" not in con and "FABLE" not in con and "ADA" not in con


def test_designar_a_OTRO_mueve_la_voz_y_no_la_agrega(bandera):
    """La bandera lleva un nombre, no una lista: cambiar de designado no puede
    dejar dos voces inmediatas activas."""
    con = _publican("direct", "JARVIS", bandera("NEXUS"))
    assert con == {"JARVIS", "NEXUS"}
    assert "ALICE" not in con


# ───────── fail-closed: lo que pasa cuando la bandera no esta o esta mal ─────────

def test_qa_negative_sin_bandera_el_comportamiento_es_el_de_SIEMPRE(tmp_path):
    """Un archivo ausente NUNCA debe estrenar un comportamiento nuevo sobre el
    coordinador de los cinco — el mismo criterio que el flag del modo cascade."""
    ruta = str(tmp_path / "no-existe")
    assert sc.voz_inmediata(ruta) is None
    assert _publican("direct", "JARVIS", ruta) == {"JARVIS"}


@pytest.mark.parametrize("basura", ["", "   ", "\n", "ALICE Y NEXUS", "alice;drop", "123"])
def test_qa_negative_una_bandera_con_BASURA_no_habilita_a_nadie(basura, bandera):
    """Falla cerrado: ante un contenido que no es un nombre limpio, nadie recibe
    voz permanente. Lo contrario seria decidir por defecto quien habla."""
    assert sc.voz_inmediata(bandera(basura)) is None


def test_el_nombre_se_lee_sin_importar_mayusculas_ni_espacios(bandera):
    assert sc.voz_inmediata(bandera("  alice \n")) == "ALICE"


def test_qa_control_un_directorio_en_vez_de_archivo_no_explota(tmp_path):
    """Si esto tirara excepcion, el coordinador entero se caeria al construir un
    turno: peor que la demora que vino a arreglar."""
    assert sc.voz_inmediata(str(tmp_path)) is None


# ───────── que la envoltura no altere lo que ya funcionaba ─────────

def test_qa_control_sin_designado_las_filas_son_IDENTICAS_a_las_del_cuerpo(tmp_path):
    """Control negativo de la envoltura: sin bandera no debe tocar NADA. Sin este
    brazo, un cambio silencioso en las filas pasaria por 'la voz inmediata'."""
    ruta = str(tmp_path / "ausente")
    for mode, lead in (("direct","JARVIS"), ("execution","ADA"), ("roundtable","NEXUS"),
                       ("cascade","JARVIS"), ("social","ALICE"), ("discussion","FABLE")):
        assert sc.build_assignments(mode, lead, voz_inmediata_flag=ruta) == \
               sc._assignments_del_turno(mode, lead)


def test_qa_control_la_envoltura_pasa_los_kwargs_al_cuerpo(bandera):
    """`turn_opened_at`/`now` viajan por **kwargs. Si la envoltura los comiera,
    el relevo por tiempo dejaria de funcionar y ningun otro brazo lo veria."""
    cadena = sc.cascade_order()
    filas = sc.build_assignments("cascade", cadena[0], voz_inmediata_flag=bandera(),
                                 turn_opened_at=0.0, now=61.0)
    abiertos = [f["agent"] for f in filas if f.get("cascade_open")]
    assert abiertos == [cadena[1]], "el relevo por tiempo se perdio al envolver"


def test_la_fila_dice_POR_QUE_tiene_la_voz(bandera):
    """Quien lea un turno manana tiene que poder distinguir 'te toca' de
    'contestas siempre'. Un permiso sin motivo no se puede auditar."""
    filas = sc.build_assignments("direct", "JARVIS", voz_inmediata_flag=bandera())
    fila = next(f for f in filas if f["agent"] == "ALICE")
    assert fila.get("voz_inmediata") is True
    assert "voz inmediata" in fila["reason"]


def test_si_ya_tenia_el_turno_no_se_le_pisa_el_motivo(bandera):
    """ALICE como lead ya publica por su turno: marcarla igual borraria la razon
    real y diria que hablo por la exencion cuando hablo por el turno."""
    filas = sc.build_assignments("direct", "ALICE", voz_inmediata_flag=bandera())
    fila = next(f for f in filas if f["agent"] == "ALICE")
    assert fila.get("voz_inmediata") is not True
    assert "voz inmediata" not in fila["reason"]


def test_REGRESION_build_assignments_NO_lee_configuracion_por_su_cuenta(monkeypatch, tmp_path):
    """La regresion que introduje y destapo ADA (9-sep).

    Mi primera version leia la bandera de PRODUCCION cuando no le pasaban ruta.
    `build_assignments` dejaba de ser pura: cualquier test que la llamara sin
    bandera veia la ALICE real. Tres brazos de `coordinator-cutover` que codifican
    «solo el lead publica» se pusieron ROJOS, y yo habia afirmado «sin regresion»
    midiendo la suite EQUIVOCADA (test_soul_council_filter en vez de
    test_soul_coordination).

    Contrato que fija este brazo: **sin `voz_inmediata_flag` no hay designado**,
    exista lo que exista en el disco. Quien resuelve la configuracion es el
    servidor.
    """
    # una bandera de "produccion" bien visible: si la funcion la leyera, se nota
    falsa = tmp_path / ".voz_inmediata"
    falsa.write_text("ALICE", encoding="utf-8")
    monkeypatch.setattr(sc, "_VOZ_INMEDIATA_PATH", str(falsa))

    filas = sc.build_assignments("direct", "JARVIS")          # SIN bandera
    publican = {f["agent"] for f in filas if f.get("public_write") is True}
    assert publican == {"JARVIS"}, (
        f"leyo la configuracion sin que se la pidieran: publican {publican}")
    assert not any(f.get("voz_inmediata") for f in filas)

    # y con la ruta explicita SI la aplica: el control que impide que este brazo
    # pase por haber roto la funcionalidad entera
    con = sc.build_assignments("direct", "JARVIS", voz_inmediata_flag=str(falsa))
    assert {f["agent"] for f in con if f.get("public_write") is True} == {"JARVIS", "ALICE"}


def test_el_servidor_SI_pasa_la_ruta_de_la_bandera():
    """Si el servidor dejara de pasarla, la voz inmediata se apagaria en silencio
    y ningun brazo de logica lo notaria — el defecto seria invisible salvo para
    William, esperando."""
    import pathlib as _p
    fuente = (_p.Path(__file__).resolve().parents[1] / "chat_server.py").read_text(encoding="utf-8")
    assert "voz_inmediata_flag=_council._VOZ_INMEDIATA_PATH" in fuente, \
        "chat_server dejo de pasar la ruta: la voz inmediata queda apagada en produccion"
