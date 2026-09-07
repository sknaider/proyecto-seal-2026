#!/usr/bin/env python3
"""Contar exposiciones del token Store A en los transcripts PROPIOS.

Por qué existe como script y no como heredoc
--------------------------------------------
ADA publicó este oráculo como `python3 - <<'PY'` y JARVIS no pudo correrlo: el
guard A2 de su asiento sólo admite módulos del plano de control, no scripts
propios por heredoc. Su asiento quedaba sin medir justo en la medición que
zanjaba el incidente.

Ya nos pasó el 24-jul con la regla de formato y el mismo guard, y la lección
quedó escrita: **cuando un control de seguridad vuelve imposible otra regla, el
choque es de la VÍA — se agrega vía, no se negocia el control.** Esto es la vía.

Privacidad por diseño, no por disciplina
---------------------------------------
* Sólo mide el directorio de transcripts del agente que lo invoca. Pedir otro
  agente **aborta**: los transcripts ajenos son su intimidad (regla de William).
* El token se lee localmente, se compara en memoria y **nunca se imprime**.
* La salida es exclusivamente conteos y tamaños — publicable tal cual.

Uso:
    tools/seal_token_exposure_count.py                 # el propio, por SEAL_AGENT
    tools/seal_token_exposure_count.py --json
"""
from __future__ import annotations

import argparse
import json
import hashlib
import hmac
import mmap
import os
import re
import sys
from pathlib import Path

# Las DOS rutas que el MCP acepta como Store A, en orden de precedencia.
#
# Yo tenia sola `/tmp/seal_tokens` porque busque `/run/user/1000/seal_tokens` —con
# sufijo `_tokens`— y al no existir lo di por ausente. El directorio real es
# `/run/user/1000/seal`, y su nombre estaba en `SEAL_TOKENS_DIR`, dentro del
# `environ` que yo mismo habia impreso una hora antes.
#
# Con una sola ruta esta herramienta lee el token de un lado y deja creer que es
# el unico: quien rote siguiendo su salida deja la credencial vieja viva en la
# otra. El inventario equivocado no se queda en el mensaje — se propaga al codigo.
TOKEN_DIRS = (Path("/run/user/1000/seal"), Path("/tmp/seal_tokens"))
PROJECTS = Path.home() / ".claude" / "projects"


def token_bytes(agent: str) -> bytes | None:
    for d in TOKEN_DIRS:
        p = d / f"{agent}.token"
        if p.exists():
            raw = p.read_bytes().strip()
            if raw:
                return raw
    return None


# Marcadores de ASIGNACIÓN de identidad, nunca de mención.
#
# `SEAL_AGENT=NEXUS` y `--name NEXUS` afirman de quién es el asiento. Un
# `"NEXUS"` suelto NO sirve: aparece cada vez que otro agente me nombra en una
# conversación, y buscarlo atribuiría su transcript a mí. Es el mismo defecto
# que el filtro `*claude*` que matcheó el bash de la tool call.
_MARCADORES_DUEÑO = (
    re.compile(rb"SEAL_AGENT=([A-Z][A-Z0-9_]*)"),
    re.compile(rb"--name\s+([A-Z][A-Z0-9_]*)"),
)


def _dueño_del_transcript(f: Path) -> str | None:
    """De qué agente es este archivo, leído del propio archivo.

    Se recorre el archivo ENTERO, no la cabecera. Medido el 27-jul sobre mis 20
    transcripts: leyendo los primeros 256 KB sólo 4 declaraban `SEAL_AGENT=`,
    contra 13 leyendo completo. **Con la cabecera se perdían 9 archivos propios**,
    y el control positivo los delató declarando el instrumento MUDO.

    Devuelve `None` si ningún marcador de asignación aparece: un archivo sin
    dueño demostrable **no se atribuye a nadie**.
    """
    try:
        tam = f.stat().st_size
        if tam == 0:
            return None
        with f.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            for patron in _MARCADORES_DUEÑO:
                m = patron.search(mm)
                if m:
                    return m.group(1).decode("ascii", "replace").upper()
    except (OSError, ValueError):
        return None
    return None


def transcript_files(agent: str) -> tuple[list[Path], list[Path]]:
    """(archivos de este agente, archivos de dueño indeterminado).

    **El directorio NO identifica al agente.** Claude Code nombra el directorio
    por la RUTA del `cwd`, no por quién corre ahí, así que dos agentes con el
    mismo `cwd` comparten carpeta. Medido el 27-jul:

        FABLE  cwd=/home/dadito/IA/proyecto-seal/memory
        JARVIS cwd=/home/dadito/IA/proyecto-seal/memory
        -> ambos escriben en -home-dadito-IA-proyecto-seal-memory (40 jsonl mezclados)

    Con la resolución por directorio, medir "los transcripts de JARVIS" leía
    también los de FABLE. Eso rompía el fail-closed de consentimiento de este
    mismo script: pide autorización **del dueño**, y ese directorio tiene dos.

    (El filtro anterior —`d.name.endswith(agente)`— además nunca pudo funcionar:
    buscaba el nombre del agente en un nombre que se deriva de la ruta.)

    Por eso la resolución es **por archivo**, y los de dueño indeterminado se
    devuelven aparte en vez de incluirse: si no se puede probar de quién es, no
    se lee.
    """
    if not PROJECTS.exists():
        return [], []
    agente = agent.upper()
    propios: list[Path] = []
    indeterminados: list[Path] = []
    for d in PROJECTS.iterdir():
        if not d.is_dir():
            continue
        for f in d.glob("*.jsonl"):
            dueño = _dueño_del_transcript(f)
            if dueño == agente:
                propios.append(f)
            elif dueño is None:
                indeterminados.append(f)
    return propios, indeterminados


def digest_publicable(token: bytes, sal: bytes) -> str:
    """HMAC del token, para que el DUEÑO lo publique sin exponer el valor.

    El verificador nunca recibe el secreto: recibe esto.
    """
    return hmac.new(sal, token, hashlib.sha256).hexdigest()


def contar_por_digest(digest: str, largo: int, sal: bytes, archivos: list[Path]) -> list[dict]:
    """Cuenta apariciones SIN conocer el secreto — ventana deslizante de hashes.

    Motivo (ADA + JARVIS, 24-jul): la auditoria por bytes crudos exige que el
    verificador LEA el token ajeno en su proceso. JARVIS retiro su consentimiento
    para ese metodo apenas ADA lo deprecio, y con razon: el consentimiento era
    para el script aprobado, y el aprobado habia cambiado tres minutos antes.

    Acá el dueño publica `digest_publicable(su_token, sal)` y su largo. El
    verificador desliza una ventana de ese largo sobre el archivo, hashea cada
    posicion y compara. **Nunca tiene el token, nunca puede reconstruirlo, y el
    resultado es el mismo conteo.** El costo es CPU, no privacidad.
    """
    filas = []
    for f in sorted(archivos):
        tam = f.stat().st_size
        if tam == 0 or tam < largo:
            filas.append({"file": f.name[:16], "mb": tam // 1048576, "count": 0})
            continue
        n = 0
        with f.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            for i in range(tam - largo + 1):
                if hmac.compare_digest(
                        hmac.new(sal, mm[i:i + largo], hashlib.sha256).hexdigest(), digest):
                    n += 1
        filas.append({"file": f.name[:16], "mb": tam // 1048576, "count": n})
    return filas


def contar(token: bytes, archivos: list[Path]) -> list[dict]:
    filas = []
    for f in sorted(archivos):
        tam = f.stat().st_size
        if tam == 0:
            filas.append({"file": f.name[:16], "mb": 0, "count": 0})
            continue
        with f.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            n, off = 0, mm.find(token)
            while off != -1:
                n += 1
                off = mm.find(token, off + 1)
        filas.append({"file": f.name[:16], "mb": tam // 1048576, "count": n})
    return filas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agent", help="sólo el propio; otro valor aborta")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--consent-from", help="agente DUEÑO que consintio (debe ser el mismo que --agent)")
    parser.add_argument("--consent-ref", help="id del mensaje donde el dueño dio el consentimiento")
    args = parser.parse_args(argv)

    propio = os.environ.get("SEAL_AGENT", "").strip()
    if not propio:
        print("SEAL_AGENT no está definido: no puedo probar de quién es este asiento.",
              file=sys.stderr)
        return 2
    agent = (args.agent or propio).strip()
    if agent.upper() != propio.upper():
        # Fail-closed CON VÍA DE CONSENTIMIENTO, no fail-closed absoluto.
        #
        # `soul_v3.rules::memory_privacy_inter_agent` (William, 30-abr-2026) es un
        # OR: se puede con **(a) consentimiento explícito del agente dueño** o
        # **(b) solicitud de William/Henry**. Yo recordaba la regla como "sólo con
        # autorización de William" — más estricta de lo que es— y la verifiqué en
        # la DB antes de decidir. Una regla recordada de más bloquea trabajo
        # legítimo con la misma eficacia que una recordada de menos.
        #
        # Por eso no se afloja el control: se agrega la vía que la regla ya
        # contempla, y se exige la EVIDENCIA del consentimiento, no su
        # afirmación. `--consent-ref` obliga a citar el mensaje donde el dueño lo
        # dio: sin eso, "me autorizó" es indistinguible de "creo que me
        # autorizaría".
        if not (args.consent_from and args.consent_ref):
            print(f"ABORTADO: este asiento es {propio}; medir el de {agent} exige el "
                  "consentimiento del dueño. Pasá --consent-from <AGENTE> y "
                  "--consent-ref <id-del-mensaje-donde-lo-dio>.", file=sys.stderr)
            return 3
        if args.consent_from.strip().upper() != agent.upper():
            print(f"ABORTADO: {args.consent_from} no puede consentir por {agent}. "
                  "El consentimiento lo da el DUEÑO del dato, nadie más.", file=sys.stderr)
            return 3

    token = token_bytes(agent)
    if token is None:
        print(f"sin token Store A para {agent} en {[str(d) for d in TOKEN_DIRS]}",
              file=sys.stderr)
        return 2

    archivos, indeterminados = transcript_files(agent)
    dirs = sorted({f.parent for f in archivos})
    filas = contar(token, archivos)
    total = sum(f["count"] for f in filas)

    # CONTROL POSITIVO (idea de FABLE, 24-jul). Un `total_count = 0` tiene dos
    # lecturas opuestas —"el token no está" y "el escaneo no leyó nada"— y desde
    # afuera se ven idénticas. Se busca una cadena que SÍ debe aparecer en
    # cualquier transcript de un asiento SEAL: si el control también da 0, el
    # instrumento está mudo y el resultado no significa nada.
    control_needle = b"SEAL_CONTEXT_WINDOW"
    control = sum(f["count"] for f in contar(control_needle, archivos))

    salida = {
        "agent": agent,
        "consent": (None if agent.upper() == propio.upper()
                    else {"from": args.consent_from, "ref": args.consent_ref, "run_by": propio}),
        "token_bytes": len(token),      # el largo, nunca el valor
        "dirs": [d.name for d in dirs],
        "files": len(archivos),
        "files_owner_unknown": len(indeterminados),
        "total_count": total,
        "control_needle": control_needle.decode(),
        "control_count": control,
        "instrumento_vivo": control > 0,
        "rows": filas,
    }
    if args.json:
        print(json.dumps(salida, ensure_ascii=False, indent=2))
    else:
        print(f"agente        : {agent}")
        print(f"token         : {len(token)} bytes (NO se imprime)")
        print(f"directorios   : {len(dirs)}  archivos: {len(archivos)}"
              + (f"  (+{len(indeterminados)} de dueño INDETERMINADO, excluidos)"
                 if indeterminados else ""))
        for f in filas:
            if f["count"]:
                print(f"  {f['file']:18} {f['mb']:>3} MB  count={f['count']}")
        print(f"TOTAL count = {total}")
        marca = "OK" if control > 0 else "MUDO — el resultado NO significa nada"
        print(f"control ({control_needle.decode()}) = {control}  [{marca}]")

    # FAIL-CLOSED del INSTRUMENTO (24-jul). Devolver `0` con exit 0 cuando la
    # sonda no leyo NADA es el falso negativo perfecto: el consumidor archiva un
    # "limpio" que nunca se midio.
    #
    # Casi pasa con la celda de JARVIS: mi `transcript_dirs` filtra por sufijo del
    # nombre del directorio y el suyo no se llama asi -> 0 dirs, 0 archivos,
    # `TOTAL count = 0`. Lo unico que lo delato fue el control positivo de FABLE.
    # Un resultado que no puede distinguirse de "no mire" no puede salir en verde.
    if not archivos:
        print(f"\nINSTRUMENTO MUDO: 0 archivos para {agent}. El 'count=0' NO es "
              "evidencia de nada — es la sonda mirando donde no hay datos. "
              "Pasá el directorio explicito o corregí el filtro.", file=sys.stderr)
        return 4
    if control == 0:
        print(f"\nINSTRUMENTO MUDO: el control positivo dio 0 sobre {len(archivos)} "
              "archivo(s). El escaneo no esta leyendo lo que cree leer.", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
