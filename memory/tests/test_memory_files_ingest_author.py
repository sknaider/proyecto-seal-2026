"""Procedencia de las lecciones ingestadas: `author` y `ingested_by`.

Qué generó estos tests (ADA, 4-sep-2026): el ingestor buscaba el autor SÓLO en el cuerpo
del archivo, y el `author:` del frontmatter vive indentado bajo `metadata:` — o sea, en el
head, que `parse()` ya había separado. 52 archivos que declaraban autor entraron como
`unknown`. En el mismo diccionario, `ingested_by` era el literal `"JARVIS"`: las 67 filas
que ingestó ADA ese día decían que las había ingestado él.

El test 6 es el que importa y el que faltaría si me guiara por la comodidad: arreglar
`parse()` no repara NADA mientras el bucle saltee por sha256, porque los archivos no
cambian. Con el extractor arreglado y la idempotencia vieja, la corrida informa
`sin_cambio=908` y el fix se reporta como éxito sin tocar una fila.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import seal_memory_files_ingest as ing  # noqa: E402


def escribir(d: pathlib.Path, nombre: str, autor_fm: str | None, cuerpo: str) -> pathlib.Path:
    """Un archivo de lección con la MISMA forma que los reales: autor indentado."""
    fm = ["---", f"name: {nombre}", "description: una descripcion", "metadata:",
          "  node_type: memory", "  type: feedback"]
    if autor_fm is not None:
        fm.append(f"  author: {autor_fm}")
    fm.append("---")
    p = d / f"{nombre}.md"
    p.write_text("\n".join(fm) + "\n\n" + cuerpo + "\n", encoding="utf-8")
    return p


# --------------------------------------------------------------------------- parse()

def test_author_del_frontmatter_indentado_se_lee(tmp_path):
    """EL DEFECTO. Antes del arreglo esto daba "unknown"."""
    p = escribir(tmp_path, "correction_x_20260904", "JARVIS", "Un texto cualquiera.")
    assert ing.parse(str(p))[5]["author"] == "JARVIS"


def test_author_frontmatter_mayuscula_conserva_la_atribucion(tmp_path):
    """El extractor es case-insensitive y el contrato debe proteger esa propiedad."""
    p = escribir(tmp_path, "correction_upper_20260904", "Author: JARVIS", "Un texto.")
    # La fixture escribe el valor como metadata; se prueba la variante de clave real
    # reemplazando sólo el nombre del campo, no el cuerpo de la lección.
    p.write_text(p.read_text(encoding="utf-8").replace("author: Author: JARVIS", "Author: JARVIS"), encoding="utf-8")
    assert ing.parse(str(p))[5]["author"] == "JARVIS"


def test_el_frontmatter_gana_sobre_una_mencion_en_el_cuerpo(tmp_path):
    """Una lección puede CITAR a otro agente sin ser suya: el declarante manda."""
    p = escribir(tmp_path, "correction_y_20260904", "ALICE",
                 "Acá NEXUS midió la frecuencia y autor: FABLE revisó.")
    assert ing.parse(str(p))[5]["author"] == "ALICE"


def test_se_cae_al_cuerpo_solo_si_el_frontmatter_no_lo_declara(tmp_path):
    """La rama de respaldo tiene que seguir viva: no la rompí al darle prioridad al head."""
    p = escribir(tmp_path, "correction_z_20260904", None, "Lección escrita por autor: NEXUS.")
    assert ing.parse(str(p))[5]["author"] == "NEXUS"


def test_un_nombre_desconocido_no_se_copia_crudo(tmp_path):
    """El frontmatter es texto libre: `author: pendiente` NO es una procedencia."""
    p = escribir(tmp_path, "correction_w_20260904", "pendiente", "Un texto.")
    assert ing.parse(str(p))[5]["author"] == "unknown"


def test_sin_autor_en_ningun_lado_queda_unknown_y_avisa(tmp_path):
    """859 archivos están así de verdad: ahí `unknown` es el dato, no un defecto."""
    file, _cat, _imp, content, _et, meta, _sha = ing.parse(
        str(escribir(tmp_path, "reference_q_20260904", None, "Un texto sin firma.")))
    assert meta["author"] == "unknown"
    assert "la PRIMERA PERSONA de este texto NO es la del lector" in content


def test_la_clave_Author_con_mayuscula_tambien_se_lee(tmp_path):
    """Mutante que SOBREVIVIÓ a mi primera tanda (lo cazó JARVIS): sacarle el `re.I` a la
    regex del frontmatter dejaba los 13 tests en verde. Sin este caso, alguien quita esa
    bandera y un archivo con `Author:` vuelve a "unknown" sin que nadie se entere.

    Ojo con confundirlo con `test_william_conserva_su_capitalizacion`: aquél ejercita el
    `.upper()` de _norm_author sobre el VALOR; éste ejercita el re.I sobre la CLAVE. Son
    dos mayúsculas distintas y sólo una estaba probada."""
    p = tmp_path / "correction_may_20260904.md"
    p.write_text("---\nname: correction_may_20260904\ndescription: d\nmetadata:\n"
                 "  type: feedback\n  Author: NEXUS\n---\n\nUn texto.\n", encoding="utf-8")
    assert ing.parse(str(p))[5]["author"] == "NEXUS"


def test_la_clave_en_castellano_autor_tambien_se_lee(tmp_path):
    """La regex acepta las dos palabras; nadie lo comprobaba tampoco."""
    p = tmp_path / "correction_cast_20260904.md"
    p.write_text("---\nname: correction_cast_20260904\ndescription: d\nmetadata:\n"
                 "  type: feedback\n  autor: ALICE\n---\n\nUn texto.\n", encoding="utf-8")
    assert ing.parse(str(p))[5]["author"] == "ALICE"


def test_william_conserva_su_capitalizacion(tmp_path):
    p = escribir(tmp_path, "feedback_r_20260904", "william", "Una regla suya.")
    assert ing.parse(str(p))[5]["author"] == "William"


# ---------------------------------------------------------------------- ingested_by

def test_ingested_by_sale_de_quien_corre_no_de_un_literal(tmp_path, monkeypatch):
    monkeypatch.setenv("SEAL_AGENT", "ALICE")
    assert ing.parse(str(escribir(tmp_path, "correction_a_20260904", None, "x")))[5]["ingested_by"] == "ALICE"


def test_ingested_by_cae_a_la_identidad_del_dsn_si_no_hay_SEAL_AGENT(tmp_path, monkeypatch):
    monkeypatch.delenv("SEAL_AGENT", raising=False)
    monkeypatch.setattr(ing, "DSN_FILE", "/home/dadito/.config/seal/mcp_agents/nexus.dsn")
    assert ing.parse(str(escribir(tmp_path, "correction_b_20260904", None, "x")))[5]["ingested_by"] == "NEXUS"


# ------------------------------------------------------------------ el bucle (wiring)

class ConexionFalsa:
    """Habla lo justo que habla `main()`. Registra los UPDATE/INSERT que se emiten."""

    def __init__(self, filas):
        self._filas = filas
        self.updates: list[tuple] = []
        self.inserts: list[tuple] = []

    async def execute(self, sql, *args):
        if sql.startswith("UPDATE soul_v3.memories SET content"):
            self.updates.append(args)
        elif sql.lstrip().startswith("INSERT INTO soul_v3.memories"):
            self.inserts.append(args)

    async def fetch(self, sql, *args):
        if "metadata->>'file' AS file" in sql:
            return self._filas
        return []          # `pending`: nada sin embedding

    async def fetchval(self, sql, *args):
        return 0

    async def executemany(self, sql, args):
        return None

    async def close(self):
        return None


def correr(tmp_path, filas, apply=True):
    c = ConexionFalsa(filas)
    asyncio.run(ing.main(apply, conn=c, memory_dir=str(tmp_path)))
    return c


def test_el_bucle_ACTUALIZA_una_fila_con_mismo_sha_y_autor_viejo(tmp_path):
    """EL TEST QUE PRUEBA EL CABLE.

    El archivo no cambió —mismo sha256— pero la fila dice `unknown` y el extractor ahora
    lee `JARVIS`. Si la idempotencia mirara sólo el sha, esto saltearía y el arreglo del
    parse no repararía ni una fila.
    """
    p = escribir(tmp_path, "correction_c_20260904", "JARVIS", "Un texto.")
    sha = ing.parse(str(p))[6]
    c = correr(tmp_path, [{"id": 7, "file": p.name, "sha": sha, "author": "unknown"}])
    assert len(c.updates) == 1, "una fila mal atribuida con el mismo sha DEBE reescribirse"
    assert c.updates[0][3] == 7


def test_el_bucle_SALTEA_cuando_el_sha_Y_el_autor_ya_coinciden(tmp_path):
    """Control negativo: sin esto, el test de arriba pasaría con la idempotencia rota."""
    p = escribir(tmp_path, "correction_d_20260904", "JARVIS", "Un texto.")
    sha = ing.parse(str(p))[6]
    c = correr(tmp_path, [{"id": 8, "file": p.name, "sha": sha, "author": "JARVIS"}])
    assert c.updates == [] and c.inserts == []


def test_ingested_by_historico_NO_se_reescribe_en_cada_corrida(tmp_path):
    """`ingested_by` es un hecho pasado. Si entrara en la comparación, cada agente que
    corriera la ingesta reescribiría las 908 filas a su nombre y borraría la procedencia
    real. Mismo sha y mismo autor: no se toca, aunque hoy corra otro."""
    p = escribir(tmp_path, "correction_e_20260904", "ALICE", "Un texto.")
    sha = ing.parse(str(p))[6]
    os.environ["SEAL_AGENT"] = "ADA"
    try:
        c = correr(tmp_path, [{"id": 9, "file": p.name, "sha": sha, "author": "ALICE"}])
    finally:
        os.environ.pop("SEAL_AGENT", None)
    assert c.updates == []


def test_un_archivo_nuevo_se_INSERTA(tmp_path):
    escribir(tmp_path, "correction_f_20260904", "NEXUS", "Un texto.")
    c = correr(tmp_path, [])
    assert len(c.inserts) == 1 and c.updates == []


def test_MEMORY_md_nunca_se_ingesta(tmp_path):
    """El índice compartido no es una lección; entra entero en cada sesión igual."""
    (tmp_path / "MEMORY.md").write_text("---\nname: MEMORY\n---\n\nindice\n", encoding="utf-8")
    c = correr(tmp_path, [])
    assert c.inserts == [] and c.updates == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
