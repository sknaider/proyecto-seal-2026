#!/usr/bin/env python3
"""TRÁNSITO ficha -> código: convierte una conclusión escrita en un chequeo que CORRE.

Nació del hallazgo de los cinco el 31-ago-2026: tres conclusiones correctas,
escritas en las fichas de memoria, con el código sin tocar días o semanas
después. La ficha describe; no ejecuta. Este archivo ejecuta.

CONTRATO: cada regla nombra un ARCHIVO y una CONDICIÓN verificable.
  - condición cumplida  -> VERDE
  - condición violada   -> ROJO, con la línea exacta
  - archivo ausente     -> INDETERMINADO, nunca verde
El tercer estado importa: un archivo que no existe no es una regla cumplida.

Uso:  python3 tools/transito/transito_ficha_codigo.py [--self-test]
      python3 tools/transito/transito_ficha_codigo.py --ratchet
      python3 tools/transito/transito_ficha_codigo.py --baseline-write

MODO TRINQUETE (--ratchet). Bloquear con los 11 rojos de hoy dejaria el repo
sin poder commitear y el gate se apagaria en una hora: un control que todos
puentean CERTIFICA en vez de frenar. El trinquete bloquea solo lo que EMPEORA:
un rojo que no esta en la linea base. Los rojos historicos se reportan.

FALLA CERRADO A PROPOSITO. Sin linea base -> rc=3 y bloquea. Es literalmente el
defecto que este archivo caza en gate.py:263 (`return []` = "sin regresion"
cuando falta la base). Un insumo ausente da INDETERMINADO, nunca permiso.

LIMITE DECLARADO: lee el ARBOL DE TRABAJO, no el indice. Un cambio staged y
luego revertido en disco no se ve. No digo que verifica lo que se commitea:
verifica el arbol en el momento del commit.

rc: 0 sin rojos nuevos · 1 hay rojos (modo normal) · 2 rojos NUEVOS · 3 sin base
"""
from __future__ import annotations
import re, sys, json, pathlib, datetime

REPO = pathlib.Path(__file__).resolve().parents[2]

# (id, ficha de origen, archivo, patron PROHIBIDO, por que)
# Por CLASE de regla: si este patron esta, el sujeto se considera reparado.
REDENTORES = {
    "emisor-sin-recibo": re.compile(r"seal_send"),
}

REGLAS = [
    ("diff-filter-acota",
     "reference_a_grep_tells_you_where_never_what_20260829.md (ALICE, 29-ago)",
     "quality_gate/gate.py",
     re.compile(r"--diff-filter=[A-Z]+"),
     "el acote omite estados que git puede inventar (T de symlink, U). "
     "El DEFAULT los lista todos: hay que QUITAR el acote, no ampliarlo."),

    ("core-guard-acota-mas",
     "hallazgo FABLE 8 (31-ago): el guard del commit acota MAS que el gate",
     "scripts/seal_core_guard.py",
     re.compile(r"--diff-filter=[A-Z]+"),
     "seal_core_guard usa ACMR: omite ademas D (borrado). Una ruta protegida "
     "que se BORRA no dispara el guard."),

    ("catchup-sin-escritor",
     "hallazgo ALICE H8 + causa NEXUS (31-ago)",
     "jarvis_fresh.sh",
     None,  # regla POSITIVA: ver EXIGIDOS
     "el launcher manda leer /tmp/<ag>_chat_catchup.json; si no lo escribe, "
     "el paso 3 del protocolo post-compactacion apunta al vacio."),
]

# CONTRATO 1 — "el emisor devuelve recibo y el llamador lo mira".
# Acotado a los avisadores VIVOS y rotos que medi el 31-ago (censo: 34 de 85
# publicadores postean sin credencial; 5 corren hoy). No pongo los 34: una regla
# permanentemente roja se apaga sola. Estos cinco se van poniendo verdes a medida
# que sus duenos los reparan, que es lo que una regla tiene que hacer.
_AVISADORES_ROTOS = [
    "memory/denial_tracker.py",
    "memory/denial_tracking_hook.py",
    "memory/hooks/post_edit_diffcheck.py",
    "memory/idle_curiosity.py",
    "messages/ada_codex_stream_relay.py",
    "messages/peer_health_check.sh",
]
# El patron PROHIBIDO es postear al endpoint sin pasar por el escritor autenticado.
for _f in _AVISADORES_ROTOS:
    REGLAS.append((
        "emisor-sin-recibo:" + _f,
        "auditoria JARVIS 31-ago, hallazgo 1: 45 alertas emitidas, 0 entregadas",
        _f,
        re.compile(r"api/agents/send"),
        "postea crudo al endpoint (HTTP 401 en ENFORCE) sin mirar el retorno. "
        "El escritor autenticado es scripts/seal_send.py y devuelve un id.",
    ))

# CONTRATO 2 — "tres estados, nunca dos: verde · rojo · INDETERMINADO".
# Causa 8 de la sintesis: un control que pierde su insumo decide DEJAR PASAR,
# escrito en el `except`. No es una lectura ambigua: es una decision permisiva.
# Hallada por ALICE (chat_server), agrupada por FABLE (los dos del gate).
# Verificacion que propuso FABLE y afino ALICE: llamar a la funcion que decide
# con un insumo falso -- NO borrar el insumo real.
_FALLA_ABIERTO = [
    # DOS bocas por funcion, no una (ALICE, 31-ago 04:05): el `except` cubre el
    # archivo ausente y el `else "OFF"` final cubre un valor NO RECONOCIDO.
    # Un archivo corrupto o con un modo mal escrito cae igual de abierto.
    ("messages/chat_server.py",
     re.compile(r'except OSError:\s*\n\s*mode = os\.environ\.get\([^)]*"OFF"'
                r'|return mode if mode in \{[^}]*\} else "OFF"'),
     'falla ABIERTO por dos caminos: archivo ausente (except -> "OFF") y valor no '
     'reconocido (else -> "OFF"). Un insumo ausente o corrupto deberia dar '
     'INDETERMINADO y fallar CERRADO, no OFF.'),
    ("quality_gate/gate.py",
     re.compile(r"previous = _git_json\([^)]*\)\s*\n\s*if previous is None:\s*\n\s*return \[\]"),
     'sin base de comparacion -> return [] = "sin regresion". El trinquete queda '
     'apagado justo para lo que no tiene historia, que es lo que mas cambia.'),
]
for _f, _pat, _por in _FALLA_ABIERTO:
    REGLAS.append(("falla-abierto:" + _f, "causa 8 — ALICE + FABLE, 31-ago", _f, _pat, _por))

# (id, archivo, patron EXIGIDO)
# ALCANCE: la regla debe cubrir TODOS los sujetos de la clase, no el que uno
# ya arreglo. La primera version miraba solo jarvis_fresh.sh -- el que yo habia
# reparado 3 minutos antes-- y daba VERDE con nexus_fresh.sh, ada_fresh.sh y
# fable.sh rotos. Verde falso hallado por NEXUS revisando.
# "Un oraculo cuyo alcance elegis vos sigue siendo tu hipotesis": el predicado
# que no llamas da verde.
_LAUNCHERS = [
    "jarvis.sh", "jarvis_fresh.sh", "ada.sh", "ada_fresh.sh",
    "alice.sh", "alice_fresh.sh", "nexus.sh", "nexus_fresh.sh", "fable.sh",
]
EXIGIDOS = [
    ("catchup-sin-escritor:" + f, f, re.compile(r"CATCHUP_FILE="))
    for f in _LAUNCHERS
]


def _leer(rel: str) -> str | None:
    p = REPO / rel
    try:
        return p.read_text(errors="replace")
    except Exception:
        return None


def evaluar() -> list[tuple[str, str, str]]:
    """Devuelve (estado, id, detalle). Estados: VERDE / ROJO / INDETERMINADO."""
    out = []
    for rid, ficha, arch, prohibido, motivo in REGLAS:
        src = _leer(arch)
        if src is None:
            out.append(("INDETERMINADO", rid, f"{arch}: no se pudo leer"))
            continue
        if prohibido is not None:
            # REDENTOR: un patron prohibido NO alcanza para condenar. El guardia
            # que YO reparé seguía nombrando el endpoint en una constante muerta
            # y en un comentario: la regla lo marcaba ROJO estando arreglado.
            # Un predicado que no distingue el sujeto reparado del roto no es un
            # control, es un generador de falsos positivos.
            redentor = REDENTORES.get(rid.split(":", 1)[0])
            if redentor is not None and redentor.search(src):
                out.append(("VERDE", rid, f"{arch}: usa el camino redimido"))
                continue
            # MULTILINEA: el motor evaluaba LINEA POR LINEA, asi que un patron
            # que abarca dos renglones -como un `except:` y su asignacion- no
            # podia coincidir NUNCA y la regla salia VERDE. Un verde imposible es
            # el mismo defecto que estas reglas existen para cazar. Ahora busco
            # sobre el texto entero y derivo la linea del offset del match.
            hits = []
            for m in prohibido.finditer(src):
                linea = src.count("\n", 0, m.start()) + 1
                hits.append((linea, m.group(0).splitlines()[0].strip()))
            if hits:
                # El display cortaba en 4 SIN decirlo: sobre chat_server detectaba
                # 6 bocas y mostraba 4. Un tablero que recorta la evidencia sin
                # declarar el recorte hace exactamente lo que estas reglas cazan.
                det = "; ".join(f"{arch}:{i}" for i, _ in hits[:4])
                if len(hits) > 4:
                    det += f"  (+{len(hits) - 4} mas, {len(hits)} en total)"
                out.append(("ROJO", rid, f"{det}  <- {motivo}"))
            else:
                out.append(("VERDE", rid, f"{arch}: sin el patron prohibido"))
    for rid, arch, exigido in EXIGIDOS:
        src = _leer(arch)
        if src is None:
            out.append(("INDETERMINADO", rid, f"{arch}: no se pudo leer"))
        elif exigido.search(src):
            out.append(("VERDE", rid, f"{arch}: el escritor esta presente"))
        else:
            out.append(("ROJO", rid, f"{arch}: falta {exigido.pattern}"))
    return out


def self_test() -> int:
    """Un self-test necesita un caso ROJO y uno VERDE. Sin el rojo no verifica nada."""
    import tempfile, os
    ok = True
    # ROJO sintetico: un patron que SI esta
    r = re.compile(r"--diff-filter=[A-Z]+")
    if not r.search("git diff --diff-filter=ACMRD"):
        print("  self-test ROJO: FALLO (el patron no detecta lo que debe)"); ok = False
    else:
        print("  self-test ROJO: ok (detecta el acote)")
    # VERDE sintetico: el mismo patron cuando NO esta
    if r.search("git diff --name-only"):
        print("  self-test VERDE: FALLO (detecta donde no hay)"); ok = False
    else:
        print("  self-test VERDE: ok (no detecta donde no hay)")
    # INDETERMINADO: archivo ausente no puede dar verde
    if _leer("no/existe/jamas.py") is not None:
        print("  self-test INDETERMINADO: FALLO"); ok = False
    else:
        print("  self-test INDETERMINADO: ok (archivo ausente -> None, nunca verde)")
    return 0 if ok else 1


BASE = pathlib.Path(__file__).resolve().parent / "baseline_rojos.json"


def _no_verdes(res):
    """ROJO e INDETERMINADO. Un archivo ausente no es una regla cumplida."""
    return {rid for estado, rid, _ in res if estado != "VERDE"}


def baseline_write() -> int:
    res = evaluar()
    congelados = sorted(_no_verdes(res))
    BASE.write_text(json.dumps({
        "generado": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "por": "JARVIS",
        "que_es": "deuda ACEPTADA el dia que se escribio. El trinquete bloquea lo "
                  "que NO este aca. Esta lista solo puede ACHICARSE.",
        "no_verdes": congelados,
    }, indent=2, ensure_ascii=False) + "\n")
    print(f"  linea base escrita: {len(congelados)} reglas no-verdes congeladas")
    for rid in congelados:
        print(f"    - {rid}")
    print(f"  -> {BASE.relative_to(REPO)}")
    print("  Esta lista solo puede achicarse. Si crece, alguien la reescribio.")
    return 0


def ratchet() -> int:
    try:
        base = set(json.loads(BASE.read_text())["no_verdes"])
    except Exception as e:
        print(f"[TRANSITO] INDETERMINADO: no pude leer la linea base ({e.__class__.__name__}).")
        print(f"           esperada en {BASE}")
        print("           Sin base NO doy permiso: un insumo ausente no es un verde.")
        print("           Generala con:  python3 tools/transito/transito_ficha_codigo.py --baseline-write")
        return 3

    res = evaluar()
    ahora = _no_verdes(res)
    nuevos = sorted(ahora - base)
    reparados = sorted(base - ahora)
    detalle = {rid: det for _, rid, det in res}

    if reparados:
        print(f"[TRANSITO] {len(reparados)} regla(s) se pusieron VERDES desde la linea base:")
        for rid in reparados:
            print(f"           + {rid}")
        print("           Baja el trinquete con --baseline-write (solo puede achicarse).")

    if nuevos:
        print()
        print(f"[TRANSITO] BLOQUEADO: {len(nuevos)} regla(s) NUEVAS no verdes en este arbol.")
        for rid in nuevos:
            print(f"           x {rid}")
            print(f"             {detalle.get(rid, '')}")
        print()
        print("           No bloqueo por la deuda vieja, solo por lo que EMPEORA.")
        print("           Si es intencional y aceptado, agregalo a la linea base a mano")
        print("           y decilo en el commit. No lo agregues en silencio.")
        return 2

    print(f"[TRANSITO] ok · {len(base)} no-verdes en la linea base, 0 nuevos "
          f"({len(res)} reglas evaluadas)")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    if "--baseline-write" in sys.argv:
        return baseline_write()
    if "--ratchet" in sys.argv:
        return ratchet()
    res = evaluar()
    ancho = max(len(r[1]) for r in res)
    rojo = 0
    for estado, rid, det in res:
        marca = {"VERDE": "  OK  ", "ROJO": " ROJO ", "INDETERMINADO": " ??   "}[estado]
        print(f"[{marca}] {rid:<{ancho}}  {det}")
        rojo += estado == "ROJO"
    print()
    print(f"  {len(res)} reglas · {rojo} en ROJO")
    print("  Una ficha sin regla aca es una nota, no un control.")
    return 1 if rojo else 0


if __name__ == "__main__":
    raise SystemExit(main())
