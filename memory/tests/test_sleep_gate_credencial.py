"""Suite del carril `sleep-gate-credencial-20260905` (owner ADA, revisor NEXUS).

RECONSTRUIDA el 8-sep-2026: el archivo original nunca entró a git y murió con el borrado
del home del 7-sep 01:42. Los nombres de los tests son los que declara el manifiesto
(`quality/manifests/sleep-gate-credencial-20260905.json`); lo que prueba cada uno se
rehizo desde el sujeto y desde la evidencia de la firma de NEXUS del 5-sep.

Qué defiende: `memory/sleep_gate_cron.py` tenía el DSN del rol `seal` escrito en el
código, con la contraseña rotada el 3-sep, y la consolidación nocturna fallaba todas las
noches con `InvalidPasswordError`. El arreglo es que el DSN salga de `config.settings`.

Los mutantes que esta suite tiene que matar (los de NEXUS, 5-sep):
  1. vuelve el DSN hardcodeado (`DB_URL = "postgresql://seal:...@..."`)
  2. el import de `config` se reemplaza por un objeto con un `pg_dsn` fijo
Los dos caen en `test_el_DSN_sale_de_settings_y_no_de_una_constante`.

Ninguna prueba imprime ni compara un DSN real: un test que maneja un secreto es un canal
de publicación (lección del 7-sep).
"""
from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

MEMORY = Path(__file__).resolve().parents[1]
SUJETO = MEMORY / "sleep_gate_cron.py"

# Un DSN de Postgres con contraseña incrustada: esquema, usuario, dos puntos, algo, arroba.
_DSN_CON_CLAVE = re.compile(r"postgres(?:ql)?://[^\s:/'\"]+:[^\s@'\"]+@")
# El usuario del incidente: el rol `seal`. Ni como literal completo ni como respaldo.
_ROL_DEL_INCIDENTE = re.compile(r"postgres(?:ql)?://seal:")


def _importar(nombre: str):
    """Importa el sujeto como módulo fresco bajo `nombre` (sin cachear entre tests)."""
    sys.modules.pop(nombre, None)
    if str(MEMORY) not in sys.path:
        sys.path.insert(0, str(MEMORY))
    spec = importlib.util.spec_from_file_location(nombre, SUJETO)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def _lineas_con_dsn(texto: str) -> list[str]:
    """Líneas de CÓDIGO (no comentarios) que llevan un DSN con contraseña incrustada."""
    hallazgos = []
    for linea in texto.splitlines():
        codigo = linea.split("#", 1)[0]
        if _DSN_CON_CLAVE.search(codigo):
            hallazgos.append(codigo.strip()[:40] + "…")
    return hallazgos


def test_el_modulo_IMPORTA():
    modulo = _importar("sleep_gate_cron_importa")
    assert isinstance(modulo.DB_URL, str) and modulo.DB_URL, "DB_URL vacío"
    assert modulo.DB_URL.startswith(("postgresql://", "postgres://"))
    # A propósito no se imprime ni se compara el valor: sólo su forma.


def test_el_DSN_sale_de_settings_y_no_de_una_constante(monkeypatch):
    """Si `config.settings.pg_dsn` cambia, `DB_URL` cambia con él.

    Un DSN escrito en el código (mutante 1) o un `config` falso con valor fijo dentro
    del sujeto (mutante 2) dejarían `DB_URL` distinto del centinela.
    """
    centinela = "postgresql://centinela-de-test@localhost:1/no_existe"
    config_falso = types.ModuleType("config")
    config_falso.settings = types.SimpleNamespace(pg_dsn=centinela)
    monkeypatch.setitem(sys.modules, "config", config_falso)

    modulo = _importar("sleep_gate_cron_desde_settings")
    assert modulo.DB_URL == centinela


def test_no_hay_ninguna_contrasena_escrita_en_el_codigo():
    texto = SUJETO.read_text(encoding="utf-8")
    assert _lineas_con_dsn(texto) == [], "hay un DSN de Postgres con clave en el código"


def test_la_credencial_vieja_ya_no_aparece_ni_como_respaldo():
    """Ni el DSN completo del rol `seal` ni un fallback `or "postgresql://..."`."""
    texto = SUJETO.read_text(encoding="utf-8")
    codigo = "\n".join(l.split("#", 1)[0] for l in texto.splitlines())
    assert not _ROL_DEL_INCIDENTE.search(codigo), "vuelve el DSN del rol seal"
    assert not re.search(r"""\bor\s+["']postgres""", codigo), "hay un DSN de respaldo en el código"
    assert re.search(r"^DB_URL\s*=\s*settings\.pg_dsn\s*$", codigo, re.M), "DB_URL ya no viene de settings"


def test_control_no_vacuo_el_test_anterior_puede_fallar():
    """El detector que usan los dos tests negativos SÍ ve un DSN con clave cuando lo hay."""
    con_clave = 'DB_URL = "postgresql://seal:clave-de-ensayo@localhost:5433/seal_memory"\n'
    assert _lineas_con_dsn(con_clave), "el detector no ve un DSN con clave: los negativos serían vacuos"
    assert _ROL_DEL_INCIDENTE.search(con_clave)
    # …y no se dispara con un comentario, que es donde el sujeto documenta el incidente.
    solo_comentario = "# antes: postgresql://seal:clave@localhost/x\n"
    assert _lineas_con_dsn(solo_comentario) == []
