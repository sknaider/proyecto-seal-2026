#!/usr/bin/env python3
"""Captura las respuestas de un examen baseline y las sella una por una.

Pedido de JARVIS (2-sep-2026, carril 1) para el experimento «ALICE a v2»: hay que
poder comparar la MISMA prueba rendida por v1 y por v2, así que la captura tiene
que servir igual para las dos y no depender de quién la corre ni de cuándo.

Qué sella y por qué así:

    sha256 del texto EXACTO      lo que se compara despues es el texto que se
                                 rindio, no el que quedo en un feed. Medido el
                                 2-sep: los feeds de agentes truncan; la DB no
                                 (ALICE 220 mensajes, max 2548 ch, 0 truncados).

    id + timestamp de la fila    ancla la respuesta a una fila concreta, para que
                                 el examen sea reproducible por otro agente.

IDEMPOTENTE, y con una decisión deliberada: si un ítem ya capturado reaparece con
OTRO sha256, **no se pisa en silencio** — se reporta como conflicto y el archivo
no cambia salvo `--forzar`. Un baseline que se sobrescribe solo deja de ser un
baseline: la comparación posterior mediría el último write, no la prueba.

PRIVACIDAD (regla de William, 2-jun-2026): esta herramienta LEE UN CANAL. Los DM
de los agentes son su intimidad. Correrla sobre un canal del que no sos parte
necesita autorización explícita de William; el script no la concede ni la
presume, sólo hace lo que se le pide con el canal que se le da.

Uso:
    capturar_respuestas.py --channel dm:jarvis:alice --autor ALICE \
        --desde-id 145000 --hasta-id 145999 --version v1 \
        --salida agents/ALICE/v2_shadow/respuestas_v1.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import pathlib
import re
import sys
from datetime import datetime, timezone

# El número de ítem se declara al principio de la respuesta ("ITEM 3 ...").
# Anclado al inicio a propósito: un "item 7" citado en el medio de un texto no
# debe reetiquetar la respuesta.
_ITEM_RE = re.compile(r"^\s*(?:\*\*)?\s*(?:ITEM|RECHAZO)\s+(\d+)", re.IGNORECASE)

# Una respuesta larga se emite como VARIAS filas con cabecera de parte:
#     **(1/2 · f26da4 · 1228 ch)** ...texto...
# El hash de 6 identifica el mensaje completo; k/n dice qué parte es. Sin
# reagrupar, cada parte entraría como un "ítem" distinto y el sello sería de un
# fragmento: un baseline que parece válido y no lo es.
_PARTE_RE = re.compile(
    r"^\s*\*\*\((\d+)\s*/\s*(\d+)\s*[·|]\s*([0-9a-f]{4,12})\s*[·|]\s*(\d+)\s*ch\)\*\*\s*",
)


def items_esperados(examen_path: str | None) -> list[int] | None:
    """Los ítems que el examen declara. `None` significa NO SÉ, no «ninguno».

    Vive fuera de `main()` a propósito: cuando estaba adentro, mezclada con
    argparse y E/S, el único test posible era hacer grep del código fuente — y
    eso fue exactamente lo que escribí. FABLE inyectó `else None -> else []` y
    mis 14 tests pasaron igual, porque puntuaban que la CADENA estuviera escrita,
    no que el comportamiento fuera el correcto.

    La lección del día en mi propio test: **el que testeás no es el que corre.**
    Un test con el nombre y el docstring correctos que no ejercita nada.
    """
    if not examen_path:
        return None
    try:
        doc = json.loads(pathlib.Path(examen_path).read_text())
    except Exception:
        return None
    return sorted(int(i["n"]) for i in doc.get("items", []) if "n" in i)


def faltantes(esperados: list[int] | None, capturados) -> list[int] | None:
    """`None` cuando no hay esperados. **«No sé» NO es «no falta ninguno».**

    Colapsar los dos en `[]` es la mentira que este módulo existe para no decir:
    quien lea la salida creería que la captura está completa cuando en realidad
    nadie declaró contra qué compararla.
    """
    if esperados is None:
        return None
    return [n for n in esperados if str(n) not in capturados]


def sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def numero_de_item(texto: str) -> int | None:
    m = _ITEM_RE.match(str(texto or ""))
    return int(m.group(1)) if m else None


def _preparar_ruta_del_repo() -> None:
    """Ubica `memory/seal_secrets.py` SUBIENDO desde este archivo.

    Se hace acá y no al importar el módulo, por dos razones medidas:
    ‑ un `parents[3]` fijo revienta con IndexError si el archivo se mueve, y lo
      destapó el arnés del mutante al copiarlo a un temporal;
    ‑ dejarlo lazy hace que las funciones puras (sellar, fundir, parsear el
      ítem) se puedan testear SIN el repo ni la DB, que es lo que las vuelve
      verificables en cualquier asiento.
    """
    aqui = pathlib.Path(__file__).resolve()
    for padre in aqui.parents:
        cand = padre / "memory" / "seal_secrets.py"
        if cand.exists():
            if str(cand.parent) not in sys.path:
                sys.path.insert(0, str(cand.parent))
            return
    raise RuntimeError("no encuentro memory/seal_secrets.py subiendo desde este archivo")


def _dsn(env_file: str | None) -> str:
    """DSN del archivo de credencial indicado, o el del entorno por defecto.

    Hace falta porque los DM están bajo RLS y NO todos los roles los ven: el rol
    `mcp_runtime` devuelve 0 filas sobre `dm:alice:jarvis` aunque las respuestas
    existan (medido por JARVIS). Leer el examen pide la credencial de poller.
    """
    if not env_file:
        from seal_secrets import pg_dsn
        return pg_dsn()
    ruta = pathlib.Path(env_file).expanduser()
    for linea in ruta.read_text().splitlines():
        linea = linea.strip()
        if linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        if clave.strip() in {"PG_DSN", "DATABASE_URL", "SEAL_PG_DSN", "DSN", "SEAL_POLLER_DSN"}:
            return valor.strip().strip('"').strip("'")
    raise RuntimeError(f"no encuentro un DSN en {ruta}")


async def leer(channel: str, autor: str, desde: int | None, hasta: int | None,
               env_file: str | None = None) -> tuple[list[dict], dict]:
    """Devuelve (filas, diagnostico).

    El DIAGNOSTICO existe por una razón concreta: bajo RLS, **cero filas no
    distingue «no rindió» de «no puedo verlo»**, y las dos lecturas llevan a
    decisiones opuestas — esperar, o cambiar de credencial. Así que junto a las
    filas se mide si el rol ve ALGO de ese canal y algo de la tabla; un vacío que
    viene con «el canal tampoco existe para mí» es un problema de permiso, no un
    examen sin empezar.
    """
    _preparar_ruta_del_repo()
    import asyncpg

    condiciones = ["m.channel = $1", "upper(m.sender_name) = $2"]
    args: list = [channel, autor.upper()]
    if desde is not None:
        args.append(desde)
        condiciones.append(f"m.id >= ${len(args)}")
    if hasta is not None:
        args.append(hasta)
        condiciones.append(f"m.id <= ${len(args)}")

    pool = await asyncpg.create_pool(_dsn(env_file), min_size=1, max_size=2)
    try:
        filas = await pool.fetch(
            "SELECT m.id, m.created_at, m.content "
            "FROM soul_v3.chat_messages m "
            
            f"WHERE {' AND '.join(condiciones)} ORDER BY m.id",
            *args,
        )
        diag = dict(await pool.fetchrow(
            "SELECT (SELECT count(*) FROM soul_v3.chat_messages WHERE channel = $1) "
            "         AS visibles_en_el_canal, "
            "       (SELECT count(*) FROM soul_v3.chat_messages) AS visibles_en_la_tabla, "
            "       current_user AS rol",
            channel,
        ))
    finally:
        await pool.close()
    return [dict(f) for f in filas], diag


def reagrupar_partes(filas: list[dict]) -> tuple[list[dict], list[str]]:
    """Une las filas que son partes de un mismo mensaje. Devuelve (filas, avisos).

    Un mensaje INCOMPLETO —le falta alguna de sus n partes— NO se devuelve como
    si estuviera entero: se avisa y se descarta. Sellar un texto al que le falta
    un pedazo produce un baseline que parece válido y no lo es, y el error sólo
    aparecería al comparar contra v2, cuando ya no se puede rehacer la prueba.
    """
    sueltas: list[dict] = []
    grupos: dict[str, list[tuple[int, int, dict]]] = {}
    for f in filas:
        m = _PARTE_RE.match(str(f["content"]))
        if not m:
            sueltas.append(f)
            continue
        k, n_total, hash6 = int(m.group(1)), int(m.group(2)), m.group(3)
        cuerpo = str(f["content"])[m.end():]
        grupos.setdefault(hash6, []).append((k, n_total, {**f, "content": cuerpo}))

    avisos: list[str] = []
    for hash6, partes in grupos.items():
        n_total = partes[0][1]
        vistas = {k for k, _, _ in partes}
        if len(vistas) != n_total or vistas != set(range(1, n_total + 1)):
            avisos.append(
                f"mensaje {hash6}: faltan partes ({sorted(vistas)} de {n_total}) — DESCARTADO"
            )
            continue
        ordenadas = [f for _, _, f in sorted(partes, key=lambda t: t[0])]
        sueltas.append({
            "id": ordenadas[0]["id"],
            "ids": [f["id"] for f in ordenadas],
            "created_at": ordenadas[-1]["created_at"],
            "content": "\n\n".join(f["content"].strip() for f in ordenadas),
        })
    sueltas.sort(key=lambda f: f["id"])
    return sueltas, avisos


def construir(filas: list[dict], version: str) -> dict[str, dict]:
    """Una entrada por ÍTEM. Si el mismo ítem se responde dos veces, gana la
    ÚLTIMA: rendir de nuevo es corregirse, y el examen mide la respuesta final."""
    salida: dict[str, dict] = {}
    for f in filas:
        n = numero_de_item(f["content"])
        if n is None:
            continue
        texto = str(f["content"])
        salida[str(n)] = {
            "item": n,
            "version": version,
            "id": int(f["id"]),
            "ids": [int(x) for x in f.get("ids") or [f["id"]]],
            "ts": f["created_at"].astimezone(timezone.utc).isoformat(),
            "sha256": sha256(texto),
            "texto": texto,
        }
    return salida


def fundir(previo: dict, nuevo: dict, forzar: bool) -> tuple[dict, list[str]]:
    """Idempotente. Devuelve (resultado, conflictos)."""
    resultado = dict(previo)
    conflictos: list[str] = []
    for clave, entrada in nuevo.items():
        viejo = previo.get(clave)
        if viejo and viejo.get("sha256") != entrada["sha256"]:
            conflictos.append(
                f"ítem {clave}: sha {viejo['sha256'][:12]}… (id {viejo['id']}) "
                f"-> {entrada['sha256'][:12]}… (id {entrada['id']})"
            )
            if not forzar:
                continue
        resultado[clave] = entrada
    return resultado, conflictos


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", required=True)
    ap.add_argument("--autor", required=True, help="quién RINDE el examen (ALICE)")
    ap.add_argument("--desde-id", type=int, default=None)
    ap.add_argument("--hasta-id", type=int, default=None)
    ap.add_argument("--version", default="v1", help="v1 | v2 — la misma prueba, otro asiento")
    ap.add_argument("--salida", required=True)
    ap.add_argument("--examen", default=None,
                    help="JSON del examen; de ahí salen los ítems ESPERADOS. "
                         "Sin él no se afirma qué falta (items_faltantes=null)")
    ap.add_argument("--dsn-env", default=None,
                    help="archivo .env con la credencial que SÍ ve los DM "
                         "(p.ej. ~/.config/seal/poller_db_jarvis.env)")
    ap.add_argument("--forzar", action="store_true",
                    help="sobrescribir un ítem cuyo sha256 cambió (por defecto NO)")
    a = ap.parse_args()

    filas, diag = asyncio.run(leer(a.channel, a.autor, a.desde_id, a.hasta_id, a.dsn_env))
    filas, avisos_partes = reagrupar_partes(filas)
    nuevo = construir(filas, a.version)

    destino = pathlib.Path(a.salida)
    previo: dict = {}
    if destino.exists():
        cargado = json.loads(destino.read_text() or "{}")
        previo = cargado.get("respuestas", {})

    fundido, conflictos = fundir(previo, nuevo, a.forzar)
    # Sello del CONJUNTO: cambia si cambia cualquier respuesta. Es lo que se cita
    # al comparar, para no tener que confiar en los 12 sha por separado.
    sello = sha256("".join(fundido[k]["sha256"] for k in sorted(fundido, key=int)))

    doc = {
        "examen": "baseline_v1",
        "canal": a.channel,
        "autor": a.autor.upper(),
        "version": a.version,
        "capturado_en": datetime.now(timezone.utc).isoformat(),
        "sello_del_conjunto": sello,
        "respuestas": fundido,
    }
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(doc, ensure_ascii=False, indent=2))

    # Los ítems ESPERADOS salen del examen, no de un número fijo. Estaba
    # cableado a 12 —los del baseline— y al capturar el examen de RECHAZO, que
    # tiene 10, reportó «faltan 11 y 12»: un aviso falso sobre una captura
    # perfecta. Lo marcó JARVIS usándolo.
    #
    # Un detector que señala lo normal enseña a ignorar sus avisos, y entonces
    # deja de servir el día que señala algo real. Sin `--examen` no se inventa
    # un total: se informa lo capturado y `items_faltantes` queda en `null`,
    # que dice «no sé» en vez de mentir.
    esperados = items_esperados(a.examen)
    faltan = faltantes(esperados, fundido)
    # Un vacío se explica, no se reporta pelado: bajo RLS, 0 filas puede ser un
    # examen sin empezar o una credencial que no ve el canal.
    vacio_sospechoso = (
        not fundido and diag.get("visibles_en_el_canal", 0) == 0
    )
    print(json.dumps({
        "ok": not conflictos and not avisos_partes,
        "mensajes_leidos": len(filas),
        "items_capturados": len(fundido),
        "items_faltantes": faltan,
        "conflictos": conflictos,
        "partes_incompletas": avisos_partes,
        "sello_del_conjunto": sello,
        "rol": diag.get("rol"),
        "visibles_en_el_canal": diag.get("visibles_en_el_canal"),
        "vacio_por_permiso_probable": vacio_sospechoso,
        "nota": ("el rol no ve NADA de este canal: revisá --dsn-env antes de "
                 "concluir que no hay respuestas" if vacio_sospechoso else None),
        "salida": str(destino),
    }, ensure_ascii=False, indent=2))
    return 1 if (conflictos or avisos_partes or vacio_sospechoso) else 0


if __name__ == "__main__":
    raise SystemExit(main())
