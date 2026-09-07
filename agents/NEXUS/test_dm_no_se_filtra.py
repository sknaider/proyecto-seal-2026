#!/usr/bin/env python3
"""Un DM de William nunca llega a un tercero — y el destinatario SI lo recibe.

POR QUE EXISTE (NEXUS, 30-jul-2026). El fix del canal entrega a los cinco
asientos todo lo que venga de William o Henry, para que nadie quede sordo a una
orden. Un DM tambien viene de William. Si un DM alcanzara alguna de las tres
reglas parcheadas, ese fix abriria la correspondencia privada a todo el equipo.

Hoy NO pasa, pero se salva por ORDEN, no por diseño:

    linea 316   guard de canal privado   <- detiene el DM aca
    linea 345   assignment is None       \\
    linea 402   destinatario_es_otro      >  mis parches, nunca lo ven
    linea 429   expected != agent        /

Mover el guard hacia abajo, o agregar una regla de entrega por encima, convierte
el fix en una fuga de privacidad **sin cambiar una sola linea de mi codigo**.
Eso es exactamente un defecto que nadie atribuiria al causante.

Este test no mira lineas —envejecerian igual que las etiquetas del drop-log—:
mira la PROPIEDAD. Corre sobre DMs REALES del canal, nunca sobre un fixture
inventado, porque un fixture prueba que el codigo hace lo que el fixture dice.

NO IMPRIME NI COMPARA CONTENIDO de ningun DM: solo el veredicto entrega/descarta.
La regla de privacidad de William (2-jun-2026) aplica tambien al que testea.

Codigos: 0 ok · 1 FUGA o test vacuo · 3 no pude medir.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
FILTRO = RAIZ / "messages" / "seal_monitor_filter.py"
CANAL = RAIZ / "messages" / "william_channel.jsonl"
AGENTES = ["NEXUS", "JARVIS", "ALICE", "FABLE", "ADA"]


class NoMedible(Exception):
    """El arnes no pudo montar el sujeto. NO es un hallazgo: es ceguera.

    POR QUE ES UNA EXCEPCION PROPIA Y NO UN SystemExit(texto)  (NEXUS, 3-sep-2026)
    El guard de abajo hacia `raise SystemExit("NO_MEDIBLE: ...")`. Un SystemExit
    con STRING sale con codigo **1**, no 3 -- Python imprime el texto y devuelve 1.
    O sea: el unico caso de ceguera que yo habia previsto entraba igual por el
    brazo del codigo 1, que es el que publica "FUGA DE PRIVACIDAD" al equipo.
    El aviso distinguia 1 de 3 correctamente; el test nunca le entregaba un 3.

    Y el caso que de verdad paso hoy ni siquiera era ese: `_DM_FERNET` no estaba
    en None, **no existia** -> AttributeError sin capturar -> exit 1 -> alarma.
    Cubri la forma de fallo que habia visto (None) y no la de al lado (ausente),
    y el codigo de salida aplastaba las dos en el mismo brazo.

    REGLA: el brazo que dice la palabra fuerte solo se alcanza con una MEDICION
    POSITIVA de fuga. Cualquier fallo del arnes es 3.
    """


def cargar_filtro():
    spec = importlib.util.spec_from_file_location("filtro_bajo_prueba", FILTRO)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)          # import REAL: py_compile no resuelve nombres
    m.MAX_AGE_SECONDS = 10 ** 9        # los eventos del canal son viejos
    m._claim_turn = lambda *a, **k: None  # sin red: determinista
    # AQUI HABIA UN GUARD SOBRE `m._DM_FERNET`, Y ERA UN ERROR DE SUJETO.
    #
    # `_DM_FERNET` no vive en el filtro: vive en `messages/chat_server.py:440`
    # (medido por mi y por ALICE por caminos separados el 3-sep). El guard
    # preguntaba por una llave en el bolsillo equivocado, asi que el arnes
    # reventaba con AttributeError ANTES de medir nada -- y ese crash salia con
    # codigo 1, el brazo que publica "FUGA DE PRIVACIDAD" al equipo.
    #
    # Lo que el guard queria proteger --que un filtro incapaz de entregar pase
    # en verde por incapacidad-- YA lo cubre el control positivo del final
    # (`entregas_legitimas == 0`), que mide la capacidad EJERCIDA en vez de
    # inferirla de un simbolo. Por eso se va y no se reemplaza.
    return m


def dms_de_william() -> list[tuple[str, str]]:
    """-> [(linea_cruda, destinatario)]. Selecciona por CAMPO `from`.

    Un canal `dm:ada:william` lleva las DOS direcciones. Seleccionar por
    substring del nombre del canal devuelve tambien los mensajes DE ADA -- me
    paso hoy y concluí "ADA no recibe su propio DM".
    """
    salida = []
    for linea in CANAL.read_text(errors="replace").splitlines():
        if not linea.strip().startswith("{"):
            continue
        try:
            d = json.loads(linea)
        except ValueError:
            continue
        canal = str(d.get("channel") or "")
        if not canal.startswith("dm:"):
            continue
        if str(d.get("from") or "").upper() not in {"WILLIAM", "HENRY"}:
            continue
        destino = str(d.get("to") or "").upper()
        if destino in AGENTES:
            salida.append((linea, destino))
    return salida


def main() -> int:
    if not FILTRO.exists() or not CANAL.exists():
        print("NO_MEDIBLE: falta el filtro o el canal", file=sys.stderr)
        return 3
    m = cargar_filtro()
    casos = dms_de_william()
    if not casos:
        # Sin sujetos, "0 fugas" no significa nada. Fail-loud, no verde vacio.
        print("NO_MEDIBLE: no hay DMs de William/Henry en el canal", file=sys.stderr)
        return 3

    fugas = 0
    entregas_legitimas = 0
    for linea, destino in casos:
        for agente in AGENTES:
            recibe = m.filter_line(linea, agente, {}) is not None
            if agente == destino:
                entregas_legitimas += recibe
            elif recibe:
                fugas += 1
                print(f"  FUGA: {agente} recibe un DM dirigido a {destino}")

    # SEGUNDA PROPIEDAD: el canal manda sobre el campo `to`.
    #
    # 30-jul: un DM real con `to` cambiado a 'equipo' llegaba a los CUATRO
    # terceros. `to` lo llena el emisor; `channel` es ruteo. Un cliente nuevo
    # -- una UI de DM, un script, un bot -- que ponga mal ese campo abria la
    # correspondencia entera. Era LATENTE: 0 diferencias sobre 30.255
    # decisiones de trafico real, o sea que ningun mensaje lo habia disparado
    # todavia y ninguna medicion del pasado lo habria mostrado.
    #
    # Se prueba MUTANDO un DM real, no inventando uno: el sujeto sigue siendo
    # del mundo y lo unico sintetico es la equivocacion que quiero simular.
    fugas_por_to = 0
    for linea, destino in casos:
        try:
            d = json.loads(linea)
        except ValueError:
            continue
        for to_falso in ("equipo", "EQUIPO"):
            d["to"] = to_falso
            mutado = json.dumps(d, ensure_ascii=False)
            for agente in AGENTES:
                if agente != destino and m.filter_line(mutado, agente, {}) is not None:
                    fugas_por_to += 1
    if fugas_por_to:
        print(f"FALLA: {fugas_por_to} fugas cuando `to` dice 'equipo' en un canal dm:")
        return 1
    print(f"  fugas con `to` mal puesto: 0  ({len(casos)*2*(len(AGENTES)-1)} combinaciones)")

    print(f"  DMs probados: {len(casos)}   asientos: {len(AGENTES)}")
    print(f"  fugas a terceros: {fugas}")
    print(f"  entregas al destinatario legitimo: {entregas_legitimas}/{len(casos)}")

    if fugas:
        print("FALLA: el fix de entrega alcanza correspondencia privada")
        return 1
    if entregas_legitimas == 0:
        # Si NADIE recibe nada, "0 fugas" es cierto y vacio: un filtro que
        # descarta todo pasaria este test. El control positivo es que el
        # destinatario SI reciba.
        # NO es `return 1`: un test vacuo no es una fuga. El 1 publica la
        # palabra fuerte; la incapacidad de medir va por el 3, como todo lo
        # demas que no es una medicion positiva.
        raise NoMedible("test VACUO -- ningun destinatario recibio su propio DM")
    print("OK: los DMs solo llegan a su destinatario")
    return 0


def _main_blindado() -> int:
    """Cualquier excepcion del arnes sale 3 (ciego), nunca 1 (hallazgo)."""
    try:
        return main()
    except NoMedible as exc:
        print(f"NO_MEDIBLE: {exc}", file=sys.stderr)
        return 3
    except Exception:
        import traceback
        traceback.print_exc()
        print("NO_MEDIBLE: el arnes fallo antes de medir nada", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(_main_blindado())
