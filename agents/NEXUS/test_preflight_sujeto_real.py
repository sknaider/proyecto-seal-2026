"""Los negativos del preflight, mutando el ExecStart REAL de cada unidad viva.

POR QUE EXISTE ADEMAS DE test_preflight_deniega.py:

Aquel muta una cadena SINTETICA que yo escribi. Si el sujeto real tuviera otra
forma —otro orden de flags, otra plantilla, la logica en un script en vez de la
unidad— esos diez negativos probarian que los brazos funcionan sobre una ficcion
mia, no sobre lo que corre. El test lo escribe quien tiene la hipotesis; el
sujeto no.

Aca el texto sale de `systemctl cat` de la unidad que realmente gobierna cada
clon, con la indireccion al script ya expandida, y se muta ESO. Cubre las dos
convenciones vivas (seal-user-clone@ y seal-ada-user-clone@), que no tienen la
misma forma.

NEXUS, 1-ago-2026.
"""
import sys, types, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import gate_arranque_clon as G


def _cfg(ag, us):
    return types.SimpleNamespace(agente=ag, usuario=us, sha_proyeccion=None,
                                 digest_fuente=None, unidad=None)


def _texto_real(ag, us):
    unidad, texto = G._resolver_unidad(ag, us)
    if not texto:
        return None, None
    real = "\n".join(l for l in texto.splitlines() if l.strip().startswith("ExecStart"))
    for ruta in re.findall(r"(/home/dadito/IA/proyecto-seal/[^\s]+\.sh)", real):
        p = Path(ruta)
        if p.is_file():
            real += "\n" + p.read_text(encoding="utf-8")
    return unidad, real


def _mutar_solo_ejecutable(texto: str, viejo: str, nuevo: str = "") -> str:
    """Cambia `viejo` SOLO en las lineas que ejecutan, dejando los comentarios.

    ESTA FUNCION ES EL ARREGLO DE UN DEFECTO DEL TEST, no del gate (ADA, 1-ago).

    Antes mutaba con `t.replace(viejo, "")`, que borra TODAS las apariciones —
    comentarios incluidos. Con eso el brazo 104 daba FAIL y yo lo leia como
    "discrimina". Pero el defecto real era otro: el gate buscaba el flag como
    subcadena del script ENTERO, asi que **un `docker run` sin `--read-only`
    seguia pasando mientras el comentario que lo explica siguiera ahi**. Mi
    documentacion satisfacia mi propio control.

    Mi mutacion borraba las dos apariciones a la vez, asi que nunca produjo el
    caso peligroso de verdad. **Una mutacion MAS AMPLIA que el defecto lo
    esconde:** hay que tocar exactamente lo que un atacante o un descuido
    tocaria — la linea que corre — y dejar el resto igual.
    """
    out = []
    for l in texto.splitlines():
        if not l.strip().startswith("#") and viejo in l:
            l = l.replace(viejo, nuevo)
        out.append(l)
    return "\n".join(out)


def _mutar_imagen_a_latest(texto: str) -> str:
    """Convierte las etiquetas fijas ejecutables actuales en una etiqueta móvil.

    El launcher común contiene más de una versión porque selecciona la imagen
    por instancia.  Fijar aquí una versión histórica haría que la mutación no
    tocara el sujeto real cuando cambie el tag.
    """
    out = []
    for line in texto.splitlines():
        if not line.strip().startswith("#"):
            line = re.sub(
                r"seal-user-clone:[A-Za-z0-9._-]+",
                "seal-user-clone:latest",
                line,
            )
        out.append(line)
    return "\n".join(out)


MUTACIONES = [
    # cada una toca SOLO la linea ejecutable; el comentario queda, que es
    # justamente lo que hacia pasar al control roto.
    ("104 sin --read-only",   G.brazo_p4_aislamiento,      lambda t: _mutar_solo_ejecutable(t, "--read-only")),
    ("104 sin cap-drop ALL",  G.brazo_p4_aislamiento,      lambda t: _mutar_solo_ejecutable(t, "--cap-drop ALL")),
    ("104 corre como root",   G.brazo_p4_aislamiento,      lambda t: _mutar_solo_ejecutable(t, "--user 1000:1000")),
    ("105 DSN inyectado",     G.brazo_p5_sin_credenciales, lambda t: t + " -e X_DSN=postgresql://u:p@h/db"),
    ("106 imagen :latest",    G.brazo_p6_imagen_fijada,    _mutar_imagen_a_latest),
    ("103 proyeccion sin RO", G.brazo_p3_proyeccion,       lambda t: _mutar_solo_ejecutable(t, "technical.sqlite3,readonly", "technical.sqlite3,rw")),
    # ── los dos falsos verdes que encontro ADA ──────────────────────────
    ("102 token de OTRO USUARIO del mismo agente", G.brazo_p2_token_correcto_montado,
     lambda t: _mutar_solo_ejecutable(
         _mutar_solo_ejecutable(t, ".agent_session_token_${AGENT}-u${USER_ID}",
                                ".agent_session_token_NEXUS-u999"),
         ".agent_session_token_ADA-u%i", ".agent_session_token_ADA-u999")),
]


def main() -> int:
    ok = True
    for ag in ("ALICE", "FABLE", "JARVIS", "NEXUS", "ADA"):
        unidad, real = _texto_real(ag, "u103")
        if not real:
            print(f"  {ag}: no pude resolver la unidad — SIN MEDIR")
            ok = False
            continue
        print(f"\n  {ag} -> {unidad}")
        for nombre, fn, mutar in MUTACIONES:
            sano = fn(_cfg(ag, "u103"), real).veredicto
            malo = fn(_cfg(ag, "u103"), mutar(real)).veredicto
            bien = (sano == G.PASS and malo == G.FAIL)
            ok = ok and bien
            print(f"    [{'OK ' if bien else 'REVISAR'}] {nombre:22s} "
                  f"sano={sano:5s} mutado={malo}")
    print("\n  " + ("Los negativos discriminan sobre el SUJETO REAL"
                    if ok else "ALGUN BRAZO NO DISCRIMINA SOBRE EL TEXTO REAL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
