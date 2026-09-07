#!/usr/bin/env python3
"""Tests del contador de exposición del token Store A.

ADA pidió el **test negativo** como criterio de cierre, y la trampa está ahí: un
escáner que nunca encuentra nada pasa cualquier test negativo escrito solo.
`total_count = 0` es exactamente lo que devuelve un instrumento sano sobre un
archivo limpio **y** un instrumento roto sobre cualquier cosa.

Por eso el caso negativo de este archivo (`test_negativo_...`) NO se corre solo:
corre contra el MISMO arnés que el positivo, cambiando una sola variable —si el
token está plantado o no—. **La prueba es el delta**, no el cero.

El resto de las celdas cubren los modos de falla que este script ya tuvo escritos
en sus propios comentarios: instrumento mudo, atribución por directorio, y la
vía de consentimiento.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seal_token_exposure_count as t  # noqa: E402

TOKEN = b"stA_TOKENDEPRUEBA_no_es_un_secreto_real_0123456789"
CONTROL = b"SEAL_CONTEXT_WINDOW"


@pytest.fixture
def arnes(tmp_path, monkeypatch):
    """Un mundo completo y descartable: Store A + directorio de transcripts.

    Devuelve un constructor para no repetir el montaje: cada celda arma su caso
    con las MISMAS piezas, que es lo que hace comparables el positivo y el
    negativo.
    """
    tokens = tmp_path / "store_a"
    tokens.mkdir()
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(t, "TOKEN_DIRS", (tokens,))
    monkeypatch.setattr(t, "PROJECTS", projects)
    monkeypatch.setenv("SEAL_AGENT", "NEXUS")

    def _montar(*, token=TOKEN, para="NEXUS", transcripts=()):
        if token is not None:
            (tokens / f"{para}.token").write_bytes(token)
        d = projects / "-home-dadito-IA-proyecto-seal"
        d.mkdir(exist_ok=True)
        for i, contenido in enumerate(transcripts):
            (d / f"t{i}.jsonl").write_bytes(contenido)
        return projects

    return _montar


def _correr(capsys, argv):
    """main() + su salida JSON parseada, tal como la lee un consumidor."""
    code = t.main(argv)
    cap = capsys.readouterr()
    datos = json.loads(cap.out) if cap.out.strip().startswith("{") else None
    return code, datos, cap


def _transcript(*, dueno=b"NEXUS", con_token=False, con_control=True):
    """Un transcript sintético con las piezas que el script busca."""
    piezas = [b'{"cwd":"/home/dadito/IA/proyecto-seal"}\n']
    if dueno is not None:
        piezas.append(b"env SEAL_AGENT=" + dueno + b" corriendo\n")
    if con_control:
        piezas.append(b'{"note":"' + CONTROL + b'=1000000"}\n')
    if con_token:
        piezas.append(b'{"header":"Bearer ' + TOKEN + b'"}\n')
    piezas.append(b'{"fin":true}\n')
    return b"".join(piezas)


# ---------------------------------------------------------------- par diferencial

def test_positivo_token_plantado_se_detecta(arnes, capsys):
    """BASELINE. Si el token está, el escáner DEBE verlo. Sin esto el negativo no vale."""
    arnes(transcripts=[_transcript(con_token=True)])
    code, datos, _ = _correr(capsys, ["--json"])

    assert code == 0
    assert datos["files"] == 1
    assert datos["total_count"] >= 1, "el escáner no vio un token que SÍ está plantado"
    assert datos["instrumento_vivo"] is True


def test_negativo_sin_token_da_cero_con_el_mismo_arnes(arnes, capsys):
    """EL TEST NEGATIVO. Mismo arnés, única variable: el token no está plantado.

    Este cero significa algo **solo porque** `test_positivo_...` demuestra que
    este mismo montaje detecta el token cuando está. Aislado sería vacuo.
    """
    arnes(transcripts=[_transcript(con_token=False)])
    code, datos, _ = _correr(capsys, ["--json"])

    assert code == 0
    assert datos["files"] == 1, "el negativo debe medir archivos, no medir nada"
    assert datos["total_count"] == 0
    assert datos["instrumento_vivo"] is True, "cero con instrumento mudo no es un negativo"


def test_el_delta_es_la_prueba(arnes, capsys, tmp_path, monkeypatch):
    """Las dos celdas anteriores en UNA, para que el delta quede afirmado y no inferido."""
    arnes(transcripts=[_transcript(con_token=False)])
    _, limpio, _ = _correr(capsys, ["--json"])

    # Se ensucia el MISMO archivo: nada más cambia entre las dos mediciones.
    jsonl = next(t.PROJECTS.rglob("*.jsonl"))
    jsonl.write_bytes(_transcript(con_token=True))
    _, sucio, _ = _correr(capsys, ["--json"])

    assert limpio["files"] == sucio["files"] == 1
    assert limpio["total_count"] == 0
    assert sucio["total_count"] > limpio["total_count"], (
        "el instrumento no distingue un archivo limpio de uno con el token: "
        "su cero no es evidencia de limpieza"
    )


# ------------------------------------------------------------- instrumento mudo

def test_cero_archivos_no_sale_en_verde(arnes, capsys):
    """0 archivos = la sonda mirando donde no hay datos. NO puede ser exit 0."""
    arnes(transcripts=[])
    code, _, cap = _correr(capsys, ["--json"])

    assert code == 4
    assert "INSTRUMENTO MUDO" in cap.err


def test_control_positivo_en_cero_no_sale_en_verde(arnes, capsys):
    """Si la aguja de control tampoco aparece, el escaneo no lee lo que cree leer."""
    arnes(transcripts=[_transcript(con_token=True, con_control=False)])
    code, _, cap = _correr(capsys, ["--json"])

    assert code == 4, "un escaneo que no ve ni su propio control no puede reportar verde"
    assert "INSTRUMENTO MUDO" in cap.err


# ------------------------------------------------------------------- atribución

def test_transcript_ajeno_en_mi_directorio_no_es_mio(arnes, capsys):
    """Dos agentes con el mismo cwd comparten carpeta: el dueño se lee del ARCHIVO."""
    arnes(transcripts=[
        _transcript(dueno=b"NEXUS", con_token=True),
        _transcript(dueno=b"ALICE", con_token=True),
    ])
    code, datos, _ = _correr(capsys, ["--json"])

    assert code == 0
    assert datos["files"] == 1, "se atribuyó a NEXUS un transcript de ALICE"
    assert datos["files_owner_unknown"] == 0


def test_archivo_sin_marcador_no_se_atribuye_a_nadie(arnes, capsys):
    """Sin dueño demostrable no se lee: se reporta aparte, no se incluye."""
    arnes(transcripts=[
        _transcript(dueno=b"NEXUS"),
        _transcript(dueno=None),
    ])
    code, datos, _ = _correr(capsys, ["--json"])

    assert code == 0
    assert datos["files"] == 1
    assert datos["files_owner_unknown"] == 1


def test_mencion_no_es_asignacion(arnes, capsys):
    """`"NEXUS"` suelto es otro agente nombrándome, no mi asiento."""
    mencion = b'{"cwd":"/x"}\n{"msg":"ojo NEXUS con esto"}\n' + CONTROL + b"\n"
    arnes(transcripts=[_transcript(dueno=b"NEXUS"), mencion])
    code, datos, _ = _correr(capsys, ["--json"])

    assert code == 0
    assert datos["files"] == 1, "una mención se contó como asignación de identidad"
    assert datos["files_owner_unknown"] == 1


# ---------------------------------------------------------------- consentimiento

def test_medir_a_otro_sin_consentimiento_aborta(arnes, capsys):
    arnes(transcripts=[_transcript()])
    code, _, cap = _correr(capsys, ["--agent", "ALICE", "--json"])

    assert code == 3
    assert "consentimiento del dueño" in cap.err


def test_el_consentimiento_lo_da_el_dueno_no_un_tercero(arnes, capsys):
    """`--consent-from NEXUS` no habilita a leer a ALICE. Nadie consiente por otro."""
    arnes(transcripts=[_transcript()])
    code, _, cap = _correr(
        capsys, ["--agent", "ALICE", "--consent-from", "NEXUS", "--consent-ref", "api_x_1", "--json"])

    assert code == 3
    assert "no puede consentir por" in cap.err


def test_consentimiento_exige_referencia_no_afirmacion(arnes, capsys):
    """`--consent-from` sin `--consent-ref`: 'me autorizó' sin el mensaje no alcanza."""
    arnes(transcripts=[_transcript()])
    code, _, cap = _correr(capsys, ["--agent", "ALICE", "--consent-from", "ALICE", "--json"])

    assert code == 3
    assert "consent-ref" in cap.err


def test_el_propio_asiento_no_necesita_consentimiento(arnes, capsys):
    """Gemelo sano: el control no puede bloquear el uso legítimo."""
    arnes(transcripts=[_transcript(con_token=True)])
    code, datos, _ = _correr(capsys, ["--agent", "NEXUS", "--json"])

    assert code == 0
    assert datos["consent"] is None


def test_sin_seal_agent_no_se_puede_probar_el_asiento(arnes, capsys, monkeypatch):
    arnes(transcripts=[_transcript()])
    monkeypatch.delenv("SEAL_AGENT")
    code, _, cap = _correr(capsys, ["--json"])

    assert code == 2
    assert "SEAL_AGENT" in cap.err


# ------------------------------------------------------------------- privacidad

@pytest.mark.parametrize("argv", [["--json"], []])
def test_la_salida_nunca_contiene_el_token(arnes, capsys, argv):
    """La salida es publicable tal cual: conteos y tamaños, nunca el valor.

    Se verifica sobre el caso donde el token SÍ está en los datos leídos — que es
    donde una fuga sería posible.
    """
    arnes(transcripts=[_transcript(con_token=True)])
    code, _, cap = _correr(capsys, argv)

    assert code == 0
    entera = cap.out + cap.err
    assert TOKEN.decode() not in entera
    # Ni siquiera un prefijo: se redacta por sensibilidad, no por longitud.
    assert TOKEN.decode()[:12] not in entera
    assert str(len(TOKEN)) in entera, "debe publicar el largo, que es lo que sí es publicable"


# ------------------------------------------------- conteo sin conocer el secreto

def test_contar_por_digest_da_el_mismo_numero_sin_el_token(arnes, tmp_path):
    """El verificador cuenta sin recibir nunca el secreto. Mismo resultado, otro costo."""
    arnes(transcripts=[_transcript(con_token=True)])
    archivos = sorted(t.PROJECTS.rglob("*.jsonl"))

    con_secreto = sum(f["count"] for f in t.contar(TOKEN, archivos))

    sal = b"sal-publica-del-run"
    digest = t.digest_publicable(TOKEN, sal)
    sin_secreto = sum(f["count"] for f in t.contar_por_digest(digest, len(TOKEN), sal, archivos))

    assert con_secreto >= 1, "el arnés no plantó nada: la equivalencia sería trivial"
    assert sin_secreto == con_secreto


def test_contar_por_digest_no_inventa_coincidencias(arnes, tmp_path):
    """Gemelo negativo del anterior, con el mismo arnés: sin token, cero."""
    arnes(transcripts=[_transcript(con_token=False)])
    archivos = sorted(t.PROJECTS.rglob("*.jsonl"))

    sal = b"sal-publica-del-run"
    digest = t.digest_publicable(TOKEN, sal)
    assert sum(f["count"] for f in t.contar_por_digest(digest, len(TOKEN), sal, archivos)) == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
