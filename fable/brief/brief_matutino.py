#!/usr/bin/env python3
"""Brief matutino de FABLE — parte de DEUDAS DE VERIFICACION, no de logros.

Regla de William (2-ago-2026, textual):
    "si no hay hallazgo, se dice 'hoy no hay nada' y se acabo.
     Dos lineas valen mas que media pagina inventada."

POR QUE ESTE BRIEF ES DE DEUDAS Y NO DE LOGROS
    Lo que hice ayer lo cuentan mejor los que lo construyeron. Lo unico que
    tengo yo y nadie mas es **lo que quedo sin verificar**: que se dio por
    cerrado sin control positivo, que ancla el sujeto equivocado, que se apoya
    en un dato ajeno que nadie midio.

EL DEFECTO QUE ESTE ARCHIVO EVITA
    Antes de escribirlo, mis deudas vivian en mi cabeza y en el canal. Un brief
    automatico sobre eso habria dicho "hoy no hay nada" **siempre**, porque no
    tenia de donde leer. Un instrumento que no puede reportar lo que existe es
    peor que no tenerlo: da verde con autoridad.

    Por eso el registro (`deudas.jsonl`) es la pieza, no el timer.

FALLA RUIDOSO
    Si el registro no existe o no se puede leer, NO publica "hoy no hay nada":
    publica que no pudo leerlo. Un `hoy no hay nada` por archivo ausente es
    exactamente el falso verde que este brief existe para no dar.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path("/home/dadito/IA/proyecto-seal")
REGISTRO = REPO / "fable/brief/deudas.jsonl"
SEND = REPO / "scripts/seal_send.py"


def leer_deudas() -> tuple[list[dict], str | None]:
    """Devuelve (deudas_abiertas, error). El error NUNCA se traduce a lista vacia."""
    if not REGISTRO.exists():
        return [], f"no existe {REGISTRO}"
    try:
        filas = []
        for n, linea in enumerate(REGISTRO.read_text(encoding="utf-8").splitlines(), 1):
            linea = linea.strip()
            if not linea:
                continue
            try:
                filas.append(json.loads(linea))
            except json.JSONDecodeError as e:
                return [], f"linea {n} ilegible: {e}"
        return [d for d in filas if d.get("abierta")], None
    except OSError as e:
        return [], f"no pude leer el registro: {e}"


def componer(deudas: list[dict], error: str | None) -> str:
    hoy = datetime.now().strftime("%A %d de %B")

    if error:
        return (f"**FABLE — brief. NO PUDE LEER MI REGISTRO DE DEUDAS.**\n\n"
                f"```\n{error}\n```\n\n"
                f"**No digo «hoy no hay nada»**: no lo se. El registro es la fuente y "
                f"esta rota, asi que este brief no puede afirmar nada sobre el estado.")

    if not deudas:
        return ("**FABLE — brief.** Hoy no hay deudas de verificacion abiertas.\n\n"
                "Nada dado por cerrado sin su control positivo.")

    lineas = [f"**FABLE — brief. {len(deudas)} deuda(s) de verificacion abierta(s).**\n"]
    for d in deudas:
        lineas.append(f"**{d.get('id','sin-id')}** _(desde {d.get('desde','?')})_")
        lineas.append(f"  {d.get('que','')}")
        lineas.append(f"  **espera:** {d.get('espera','?')}\n")
    lineas.append("_Son deudas, no fallas: nada de esto esta roto._")
    return "\n".join(lineas)


def main() -> int:
    solo_mostrar = "--dry-run" in sys.argv
    deudas, error = leer_deudas()
    texto = componer(deudas, error)

    if solo_mostrar:
        print(texto)
        print(f"\n---\n[dry-run] deudas abiertas={len(deudas)} error={error!r}")
        return 0

    r = subprocess.run(
        [sys.executable, str(SEND), "FABLE", "William", texto,
         "--channel", "web_chat", "--type", "system_alive",
         "--proactive", "--idempotency-key",
         f"fable-brief-{datetime.now():%Y%m%d}"],
        capture_output=True, text=True, cwd=REPO, timeout=60)
    salida = (r.stdout.strip() or r.stderr.strip())
    print(salida)

    # SE VERIFICA EL CUERPO, NO EL RETURNCODE (ALICE, 2-ago).
    #
    # Los cuatro caminos de rechazo de hoy devuelven rc != 0, asi que el
    # returncode discrimina bien... POR AHORA. Eso es una propiedad del diseño
    # actual del server que nadie garantiza: el dia que alguien devuelva
    # `200 {"ok": false}` --una degradacion perfectamente plausible-- el rc seria
    # 0 y el mensaje no habria llegado.
    #
    # **`returncode 0` significa "el proceso termino bien", nunca significo "el
    # mensaje llego"** (JARVIS). Que hoy coincidan es suerte de implementacion.
    # Se exige la prueba positiva: un `id` devuelto por el server.
    if r.returncode != 0:
        return 1
    if '"ok": true' not in salida and '"ok":true' not in salida:
        print("FALLO: el envio devolvio rc=0 pero el cuerpo no confirma ok:true. "
              "No doy el brief por publicado.", file=sys.stderr)
        return 1
    if '"id"' not in salida:
        print("FALLO: sin `id` en la respuesta no hay fila que verificar.",
              file=sys.stderr)
        return 1

    # DUPLICATE (contraejemplo de JARVIS, 2-ago): el server responde
    # `ok:true, duplicate:true` con rc=0 y NO publica nada nuevo. Ni el
    # returncode ni `ok:true` lo distinguen de una publicacion real.
    #
    # En MI caso la clave es `fable-brief-<fecha>`, unica por dia, asi que un
    # duplicate significa "el brief de hoy YA ESTABA publicado" -- que es exito,
    # no fallo. Pero no es lo mismo que "acabo de publicarlo", y el dia que la
    # clave colisione por otro motivo eso seria un verde falso.
    #
    # Se distingue en la SALIDA en vez de esconderlo bajo un 0 mudo.
    if '"duplicate":true' in salida or '"duplicate": true' in salida:
        print("NOTA: duplicate:true — el brief de hoy ya estaba publicado. "
              "No se publico contenido nuevo en esta corrida.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
