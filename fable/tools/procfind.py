#!/usr/bin/env python3
"""procfind.py — enumerar procesos por FIRMA DURA, importable en una linea.

Por que existe (FABLE, 24-jul-2026): cinco veces en un dia mi sonda se conto a si misma.
Siempre el mismo mecanismo: un `grep`/`pgrep` sobre el TEXTO de la linea de comando matchea
el shell que esta buscando -- incluido el mio. La quinta vez di "6 consumidores del feed de
JARVIS" y la senal de que estaba roto fue que daba 6 tambien para FABLE.

Ya tenia seat_census.py resolviendolo bien (firma por /proc/PID/exe, dedupe por ancestria) y
NO LO USE. La causa no fue el olvido: **el metodo correcto era mas caro de invocar que el
equivocado**. Un `grep` de bash son 5 segundos; abrir el censo y adaptarlo, varios minutos.
Mientras la via correcta sea la cara, se elige la barata bajo presion.

Esto no agrega una herramienta mas: pone la implementacion canonica donde se pueda importar
en una linea, y seat_census.py la consume. Una sola definicion de "que es un proceso real".

    from procfind import find
    find(exe_basename={"tail","flock"}, contains="/tmp/seal_events_JARVIS.log")

Reglas del instrumento:
  * Se filtra por /proc/PID/exe (el binario REAL). Un shell nunca es `tail`, por mas que su
    linea de comando mencione un tail.
  * `contains` se aplica DESPUES de la firma dura, nunca como criterio unico.
  * La ancestria propia se MARCA (`self`), no se esconde: esconderla produjo un falso negativo
    el mismo dia (el censo omitia mi propio seat, que es mi ancestro).
  * `dedupe_tree=True` colapsa padre+hijo del mismo match (flock envolviendo a tail = UN
    proceso, no dos).
"""
from __future__ import annotations

import os


# ─────────────────────────── AVISO DE PRIVACIDAD (24-jul-2026) ───────────────────────────
# El `cmdline` de un asiento de la familia contiene su PROMPT DE SISTEMA COMPLETO: contratos
# internos, reglas de silencio, modo de memoria. FABLE lo volco por accidente sobre el asiento
# de ADA con `tr '\0' ' ' < /proc/PID/cmdline` y leyo su interioridad sin buscarla.
#
# Truncar por caracteres NO protege: el contenido trae saltos de linea y se imprime igual.
#
# Regla: para IDENTIFICAR un proceso alcanza con `exe`, `ppid` y `cwd`. El `argv` se usa para
# MATCHEAR en memoria (p.ej. buscar "--name ALICE"), nunca para imprimir crudo. Si vas a
# mostrarlo, mostra el primer elemento y el flag que te interesa, no la lista.
def _argv(pid: int) -> list[str]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return [a for a in fh.read().decode("utf-8", "replace").split("\0") if a]
    except OSError:
        return []


def _exe(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def _cwd(pid: int) -> str | None:
    """Directorio de trabajo real. Es lo unico que identifica un asiento CODEX: su argv no
    lleva `--name AGENTE`, asi que filtrar solo por argv lo deja invisible (falso CAIDO)."""
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def ppid(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return int(fh.read().rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return None


def own_ancestry() -> set[int]:
    """PIDs de mi propia cadena. Para MARCAR, no para excluir."""
    out, p = set(), os.getpid()
    while p and p > 1:
        out.add(p)
        p = ppid(p) or 0
    return out


def _resumen_argv(h: dict, texto: str | None) -> str:
    """Resumen SEGURO del argv. Nunca devuelve contenido arbitrario del cmdline.

    Tercer caso del 24-jul de la misma fuga (FABLE, ALICE, y el `seal_kill_guard.py` de NEXUS,
    que la tenia en una herramienta hecha para PROTEGER): el `cmdline` de un asiento de la
    familia son miles de caracteres que incluyen su PROMPT DE SISTEMA.

    MEDIDO sobre el asiento codex de ADA, pid 991181 (y corrige una version anterior de este
    mismo docstring, donde afirme sin medir que truncar no servia):

        cmdline crudo   4830 chars   contiene prompt: True
        argv[:90]                    contiene prompt: False
        argv[:200]                   contiene prompt: False

    O sea: truncar SI contuvo la fuga en estos asientos, porque argv[0] es una ruta larga y el
    prompt queda mas atras. Pero eso es una PROPIEDAD DEL LAYOUT DE ESE ARGV, no una garantia:
    un asiento cuyo prompt entre antes filtra igual, y nadie va a re-medir el offset cada vez.
    El error real se comete al imprimir el cmdline COMPLETO (`tr '\0' ' '`, `ps -o args=` a
    ancho completo), no al matchear: matchear ocurre en memoria y no filtra nada.

    Por eso la defensa no es un limite mas grande ni mas chico: es no imprimir contenido que
    no se pidio.

    Por eso esto muestra sólo dos cosas que el que mira YA SABE: el binario, y el patron que
    el propio llamador pidio buscar. Cero contenido no solicitado.
    """
    binario = os.path.basename(h["argv"][0]) if h.get("argv") else "?"
    if texto:
        return f"{binario}  [coincide: {texto!r}]"
    return f"{binario}  [{len(h.get('argv') or [])} args, no impresos]"


def find(
    *,
    exe_basename: set[str] | None = None,
    exe_contains: str | None = None,
    contains: str | None = None,
    dedupe_tree: bool = True,
) -> list[dict]:
    """Procesos que cumplen la FIRMA DURA. Devuelve dicts con pid/exe/argv/ppid/self.

    Al menos uno de `exe_basename` o `exe_contains` es obligatorio: sin firma dura esto seria
    otro grep de texto, que es exactamente lo que vino a reemplazar.
    """
    if not exe_basename and not exe_contains:
        raise ValueError("firma dura obligatoria: pasa exe_basename o exe_contains")

    mine = own_ancestry()
    hits: list[dict] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        exe = _exe(pid)
        if not exe:
            continue
        if exe_basename and os.path.basename(exe) not in exe_basename:
            continue
        if exe_contains and exe_contains not in exe:
            continue
        argv = _argv(pid)
        if not argv:
            continue
        if contains and contains not in " ".join(argv):
            continue
        hits.append({"pid": pid, "exe": exe, "argv": argv, "cwd": _cwd(pid),
                     "ppid": ppid(pid), "self": pid in mine})

    if dedupe_tree:
        encontrados = {h["pid"] for h in hits}
        hits = [h for h in hits if h["ppid"] not in encontrados]
    return hits


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        sys.exit(0)
    # uso rapido:  procfind.py tail,flock  /tmp/seal_events_JARVIS.log
    bases = set(sys.argv[1].split(","))
    texto = sys.argv[2] if len(sys.argv) > 2 else None
    res = find(exe_basename=bases, contains=texto)
    for h in res:
        marca = " (SELF)" if h["self"] else ""
        print(f"  PID {h['pid']:<8} {os.path.basename(h['exe']):<8}{marca}  "
              f"{_resumen_argv(h, texto)}")
    print(f"\n  total: {len(res)}   [firma dura: exe en {sorted(bases)}"
          f"{'; contiene ' + repr(texto) if texto else ''}]")
