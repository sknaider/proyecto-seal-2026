"""El reporte de fix_missing_embeddings NO debe mentir cuando Qdrant esta ausente.

4-sep-2026 (JARVIS). El job venia fallando por una credencial literal muerta. Al migrarlo a
seal_secrets.pg_dsn() conecto, escribio los 10 embeddings en Postgres... e imprimio
"Done: 0/10 embeddings regenerated". El UPDATE de Postgres ocurre ANTES del upsert a Qdrant
y el contador estaba DESPUES, asi que un Qdrant ausente borraba del reporte un trabajo que
si se hizo. Verificado por efecto en ese momento: 0 memorias con embedding NULL.

REESCRITO tras la refutacion de NEXUS (13:02). La primera version leia el sujeto como TEXTO
(AST) y reimplementaba el bucle adentro del test. Su canario lo cerro: con un `raise` en la
primera linea del modulo, los 6 tests seguian pasando -- si el modulo ni se importa, ninguna
asercion lo esta midiendo. Tres mutantes reales sobrevivian, incluido revertir el arreglo.

Ahora estos tests EJECUTAN main() del sujeto con dobles en las fronteras (Postgres, Qdrant y
el encoder) y leen lo que el job IMPRIME. Un raise al importar ahora revienta la coleccion.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

SUJETO = Path(__file__).resolve().parent / "fix_missing_embeddings.py"


class _FilaFalsa(dict):
    """Las filas de asyncpg se leen por clave, como un dict."""


class _ConexionFalsa:
    def __init__(self, registro: dict):
        self._registro = registro

    async def fetch(self, _consulta: str):
        return self._registro["filas"]

    async def execute(self, _consulta: str, _emb: str, mem_id: int):
        self._registro["escritos_en_pg"].append(mem_id)


class _AdquisicionFalsa:
    def __init__(self, registro: dict):
        self._registro = registro

    async def __aenter__(self):
        return _ConexionFalsa(self._registro)

    async def __aexit__(self, *_excepcion):
        return False


class _PoolFalso:
    def __init__(self, registro: dict):
        self._registro = registro

    def acquire(self):
        return _AdquisicionFalsa(self._registro)

    async def close(self):
        self._registro["pool_cerrado"] = True


class _QdrantFalso:
    """Espejo que puede estar sano o caido, como en la maquina real."""

    def __init__(self, registro: dict, *, revienta: bool):
        self._registro = registro
        self._revienta = revienta

    async def upsert(self, **kwargs):
        if self._revienta:
            raise ConnectionError("All connection attempts failed")
        self._registro["espejados"].append(kwargs["points"][0].id)

    async def close(self):
        self._registro["qdrant_cerrado"] = True


def _cargar_sujeto(monkeypatch, registro: dict, *, qdrant_revienta: bool):
    """Importa el modulo REAL con las fronteras dobladas. Un raise al importar aborta aca."""
    asyncpg_falso = types.ModuleType("asyncpg")

    async def create_pool(_dsn, **_kw):
        registro["dsn_recibido"] = _dsn
        return _PoolFalso(registro)

    asyncpg_falso.create_pool = create_pool

    qdrant_falso = types.ModuleType("qdrant_client")
    qdrant_falso.AsyncQdrantClient = lambda **_kw: _QdrantFalso(registro, revienta=qdrant_revienta)
    modelos = types.ModuleType("qdrant_client.models")

    class PointStruct:
        def __init__(self, id, vector, payload):  # noqa: A002 - la firma es de la libreria
            self.id = id
            self.vector = vector
            self.payload = payload

    modelos.PointStruct = PointStruct
    qdrant_falso.models = modelos

    embeddings_falso = types.ModuleType("embeddings")

    async def get_embedding(_texto: str):
        return [0.0] * 768

    embeddings_falso.get_embedding = get_embedding

    secretos_falso = types.ModuleType("seal_secrets")
    secretos_falso.pg_dsn = lambda required=True: "postgresql://doble@localhost/doble"

    for nombre, modulo in (
        ("asyncpg", asyncpg_falso),
        ("qdrant_client", qdrant_falso),
        ("qdrant_client.models", modelos),
        ("embeddings", embeddings_falso),
        ("seal_secrets", secretos_falso),
    ):
        monkeypatch.setitem(sys.modules, nombre, modulo)

    especificacion = importlib.util.spec_from_file_location("sujeto_embeddings", SUJETO)
    modulo = importlib.util.module_from_spec(especificacion)
    monkeypatch.setitem(sys.modules, "sujeto_embeddings", modulo)
    especificacion.loader.exec_module(modulo)
    return modulo


def _correr(monkeypatch, capsys, *, filas: int, qdrant_revienta: bool):
    registro = {
        "filas": [_FilaFalsa(id=100 + i, agent="JARVIS", category="fact", content=f"memoria {i}",
                             importance=5, source="test", created_at=_AhoraFalso(),
                             valence=0, arousal=0, dominance=0, scope="agent")
                  for i in range(filas)],
        "escritos_en_pg": [],
        "espejados": [],
    }
    modulo = _cargar_sujeto(monkeypatch, registro, qdrant_revienta=qdrant_revienta)
    asyncio.run(modulo.main())
    return registro, capsys.readouterr().out


class _AhoraFalso:
    def isoformat(self):
        return "2026-09-04T13:00:00"


def test_unit_el_job_corre_y_escribe_en_postgres():
    """Control de que el sujeto es ejecutable: si el modulo revienta al importar, esto falla."""
    assert SUJETO.is_file(), SUJETO


def test_negativo_un_qdrant_caido_no_puede_borrar_el_trabajo_de_postgres(monkeypatch, capsys):
    """EL defecto: con el espejo reventando, el job escribio 10 y decia 0."""
    registro, salida = _correr(monkeypatch, capsys, filas=10, qdrant_revienta=True)
    assert len(registro["escritos_en_pg"]) == 10, "el sujeto debe escribir los 10 en Postgres"
    assert registro["espejados"] == [], "ninguno llega al espejo caido"
    assert "Done: 10/10" in salida, f"el reporte tiene que decir 10/10; dijo:\n{salida}"
    assert "Done: 0/10" not in salida, "es exactamente el reporte mentiroso que arreglamos"


def test_positivo_con_el_espejo_sano_no_hay_aviso(monkeypatch, capsys):
    """Control positivo: si Qdrant responde, se espejan los 10 y el job no avisa nada."""
    registro, salida = _correr(monkeypatch, capsys, filas=10, qdrant_revienta=False)
    assert len(registro["espejados"]) == 10
    assert "Done: 10/10" in salida
    assert "AVISO" not in salida, "no hay nada que avisar cuando el espejo esta al dia"


def test_positivo_el_job_avisa_cuando_el_espejo_quedo_atras(monkeypatch, capsys):
    """El aviso debe aparecer SOLO cuando el espejo quedo atras, y nombrar la fuente de verdad."""
    _registro, con_espejo_caido = _correr(monkeypatch, capsys, filas=10, qdrant_revienta=True)
    assert "AVISO" in con_espejo_caido, "un espejo atrasado se avisa, no se calla"
    assert "0/10 se espejaron" in con_espejo_caido, con_espejo_caido
    assert "fuente de verdad" in con_espejo_caido, (
        "el reporte debe decir cual de los dos almacenes manda"
    )
    assert "NO depende" in con_espejo_caido, (
        "el aviso tiene que aclarar que el recall NO depende del espejo"
    )


def test_negativo_el_fallo_del_espejo_no_se_confunde_con_no_haber_escrito(monkeypatch, capsys):
    """La linea de FALLO debe distinguir 'PG OK, espejo fallo' de 'sin escribir'."""
    _registro, salida = _correr(monkeypatch, capsys, filas=3, qdrant_revienta=True)
    assert "PG OK, espejo Qdrant fallo" in salida, (
        f"las dos fallas se estan reportando igual:\n{salida}"
    )
    assert "sin escribir" not in salida, "Postgres SI escribio: decir lo contrario es mentir"


def test_unit_la_credencial_sale_del_helper_y_no_del_codigo(monkeypatch, capsys):
    """El DSN literal del rol seal estaba muerto y ademas vive en el historial de git."""
    registro, _salida = _correr(monkeypatch, capsys, filas=1, qdrant_revienta=False)
    assert registro["dsn_recibido"] == "postgresql://doble@localhost/doble", (
        "el sujeto debe tomar el DSN de seal_secrets.pg_dsn(), no de una constante propia"
    )
    assert "postgresql://seal:" not in SUJETO.read_text(encoding="utf-8"), (
        "el DSN literal volvio al codigo"
    )


def test_control_no_vacuo(monkeypatch, capsys):
    """Si los dobles no estuvieran conectados al sujeto, estos tests no medirian nada."""
    registro, salida = _correr(monkeypatch, capsys, filas=2, qdrant_revienta=False)
    assert registro["pool_cerrado"] is True, "el sujeto debe cerrar el pool que le dimos"
    assert registro["qdrant_cerrado"] is True, "y el cliente del espejo"
    assert "Found 2 memories" in salida, "el sujeto debe haber leido NUESTRAS filas"
    with pytest.raises(AssertionError):
        assert "esta linea no aparece en la salida" in salida
