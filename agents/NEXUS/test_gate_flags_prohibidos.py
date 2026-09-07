#!/usr/bin/env python3
"""Test del brazo 109 — DENYLIST de flags que rompen el aislamiento.

ORIGEN: FABLE atacó `gate_arranque_clon.py` el 3-ago-2026 y encontró que el
brazo de aislamiento (104) es un **allowlist**: pregunta si están los seis flags
buenos. **Agregar `--privileged` no saca ninguno**, así que el gate daba verde
sobre un contenedor totalmente privilegiado. Lo confirmé por mi cuenta antes de
arreglar: `grep` de `privileged|SYS_ADMIN|--device|docker.sock` en el gate daba
**cero en los cuatro**.

POR QUÉ ESTE TEST TIENE UNA FORMA DISTINTA A LOS OTROS MÍOS — y es la parte que
me importa dejar escrita:

    test_preflight_sujeto_real.py   muta el ExecStart con .replace(), o sea
                                    SACA cosas. Detecta lo que falta.
    este                            AGREGA un flag sin sacar nada.

**Un test que sólo sabe quitar no puede fallar un allowlist**, porque el allowlist
sigue satisfecho. La forma del test tiene que espejar la forma del ataque: si el
peligro actúa por presencia, el caso peligroso tiene que ser una ADICIÓN.

CONTROL NO VACUO: el ExecStart real y sin tocar debe PASAR. Sin ese brazo, un
test donde todo deniega no distingue «detecta» de «rompe siempre».
"""
from __future__ import annotations

import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

import gate_arranque_clon as G  # noqa: E402

# Cada caso AGREGA algo al arranque sano. Ninguno quita nada.
ADICIONES = [
    ("--privileged", "anula --cap-drop ALL y da devices"),
    ("--cap-add SYS_ADMIN", "reintroduce la capability mas peligrosa"),
    ("--device /dev/kmsg", "dispositivo del host"),
    ("--pid=host", "ve procesos del host"),
    ("--net=host", "sin namespace de red"),
    ("--network=host", "forma larga"),
    ("--ipc=host", "memoria compartida con el host"),
    ("--userns=host", "uid 1000 pasa a ser el uid real"),
    ("-v /var/run/docker.sock:/var/run/docker.sock", "root en el host, de hecho"),
    ("--security-opt seccomp=unconfined", "sin filtro de syscalls"),
    ("--security-opt apparmor=unconfined", "sin perfil apparmor"),
]

# Arranque SANO de referencia: lleva los seis flags del allowlist. Es
# deliberadamente el que brazo_p4 aprueba, para que quede demostrado que p4
# sigue diciendo "aislado" mientras 108 deniega.
SANO = (
    "/usr/bin/docker run --rm --name seal-ada-u103-clone "
    "--read-only --cap-drop ALL --security-opt no-new-privileges "
    "--user 1000:1000 --pids-limit 512 --memory 4g "
    "-v /home/dadito/proj:/proj:ro seal/clone:1.4.2"
)


def main() -> int:
    fallos: list[str] = []
    cfg = None  # el brazo no usa cfg; se pasa explicito para no fingir contexto

    # ── CONTROL NO VACUO: lo sano PASA ──────────────────────────────────────
    print("── CONTROL: el arranque sano debe PASAR ──")
    b = G.brazo_p8_sin_flags_peligrosos(cfg, SANO)
    ok = b.veredicto == G.PASS
    print(f"  {'✅' if ok else '❌'} sano [{b.veredicto}] -> {b.detalle[:80]}")
    if not ok:
        fallos.append("el arranque sano NO pasa: el brazo deniega siempre (test vacuo)")

    # ── El allowlist sigue verde: por eso hacia falta el otro brazo ─────────
    print("\n── EL HUECO: p4 (allowlist) sobre un arranque PRIVILEGIADO ──")
    privilegiado = SANO.replace("--read-only", "--read-only --privileged")
    p4 = G.brazo_p4_aislamiento(cfg, privilegiado)
    p8 = G.brazo_p8_sin_flags_peligrosos(cfg, privilegiado)
    print(f"  p4 (allowlist) dice: {p4.veredicto}   <- el hueco de FABLE")
    print(f"  p8 (denylist)  dice: {p8.veredicto}   <- lo que lo cierra")
    if p4.veredicto != G.PASS:
        fallos.append(f"p4 NO da PASS sobre el privilegiado ({p4.veredicto}): "
                      "revisar si el hueco es como se reporto")
    if p8.veredicto != G.FAIL:
        fallos.append("p8 no deniega el arranque privilegiado")

    # ── ADICIONES: cada una debe DENEGAR ───────────────────────────────────
    print("\n── PELIGROSOS: agregar (no quitar) debe DENEGAR ──")
    for flag, por in ADICIONES:
        texto = f"{SANO} {flag}"
        b = G.brazo_p8_sin_flags_peligrosos(cfg, texto)
        deniega = b.veredicto == G.FAIL
        print(f"  {'✅' if deniega else '❌'} {flag:<48} {por}")
        if not deniega:
            fallos.append(f"NO detectado: {flag}")

    # ── Un flag prohibido COMENTADO no debe contar ─────────────────────────
    print("\n── un flag prohibido en un COMENTARIO no cuenta ──")
    b = G.brazo_p8_sin_flags_peligrosos(cfg, f"{SANO}\n# ojo: nunca usar --privileged aca")
    ok = b.veredicto == G.PASS
    print(f"  {'✅' if ok else '❌'} comentario ignorado")
    if not ok:
        fallos.append("un --privileged COMENTADO hace fallar el gate (falso positivo)")

    print()
    if fallos:
        for f in fallos:
            print(f"FALLO: {f}")
        return 1
    print(f"OK — {len(ADICIONES)} adiciones denegadas, sano pasa, comentario ignorado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
