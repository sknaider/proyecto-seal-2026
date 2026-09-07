"""Un subagente NO lanza subagentes — el mecanismo, no la regla escrita.

Qué protege (ADA, 4-sep-2026, IBM Bob §5): `SubAgentSpawner` no miraba quién lo llamaba,
así que un subagente podía construir su propio spawner y abrir otro nivel, y ése otro. Un
árbol que se abre solo no tiene techo: cada nivel multiplica procesos, timeouts y coste.

**El test que importa es `test_la_marca_VIAJA_al_proceso_hijo`.** Todos los demás prueban
la decisión dentro de un proceso, y la decisión no sirve de nada si la marca no cruza el
`subprocess.run`: el hijo es OTRO proceso y no ve nuestro `self._depth`. Sin ese test, un
`env=` borrado deja el árbol abierto y los otros nueve siguen en verde.
"""

import os
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.agents.subagent_spawner import (  # noqa: E402
    MAX_SPAWN_DEPTH,
    SubAgentNestingError,
    SubAgentSpawner,
    _DEPTH_ENV,
    current_depth,
)


class entorno:
    """Pone (o quita) la marca de profundidad y la restaura al salir."""

    def __init__(self, valor):
        self._valor, self._previo = valor, None

    def __enter__(self):
        self._previo = os.environ.get(_DEPTH_ENV)
        if self._valor is None:
            os.environ.pop(_DEPTH_ENV, None)
        else:
            os.environ[_DEPTH_ENV] = str(self._valor)
        return self

    def __exit__(self, *_):
        if self._previo is None:
            os.environ.pop(_DEPTH_ENV, None)
        else:
            os.environ[_DEPTH_ENV] = self._previo


# ── la lectura del nivel ──────────────────────────────────────────────────────

def test_sin_marca_soy_el_agente_principal():
    with entorno(None):
        assert current_depth() == 0


def test_con_marca_leo_mi_nivel():
    with entorno(1):
        assert current_depth() == 1


def test_una_marca_ilegible_falla_CERRADO():
    """Si no sé en qué nivel estoy, el error barato es negar un spawn; el caro es abrir
    un árbol sin techo. `no-soy-un-numero` no puede leerse como 'soy el principal'."""
    with entorno("no-soy-un-numero"):
        assert current_depth() == MAX_SPAWN_DEPTH


def test_un_negativo_no_reabre_el_permiso():
    """Control: `-5` no puede convertirse en 'menos que 0' y habilitar el spawn."""
    with entorno(-5):
        assert current_depth() == 0


# ── la decisión ───────────────────────────────────────────────────────────────

def test_el_principal_SI_puede_lanzar():
    """Control positivo. Sin esto, un guard que niega SIEMPRE pasaría los demás tests."""
    with entorno(None), tempfile.TemporaryDirectory() as tmp:
        r = SubAgentSpawner(work_dir=tmp).spawn(task="hola", agent="ADA", timeout=30)
        assert r.success, f"el agente principal debe poder lanzar: {r.error}"


def test_un_subagente_NO_puede_lanzar():
    with entorno(1), tempfile.TemporaryDirectory() as tmp:
        try:
            SubAgentSpawner(work_dir=tmp).spawn(task="hola", agent="ADA", timeout=30)
        except SubAgentNestingError as e:
            assert "no puede lanzar subagentes" in str(e)
        else:
            raise AssertionError("un subagente pudo lanzar otro subagente")


def test_spawn_async_TAMBIEN_esta_cerrado():
    """El segundo camino. Una guarda puesta sólo en `spawn()` lo deja abierto, y es el
    que usa el orquestador para paralelizar."""
    with entorno(1), tempfile.TemporaryDirectory() as tmp:
        sp = SubAgentSpawner(work_dir=tmp)
        fallo = []
        sp.spawn_async(task="hola", agent="ADA", timeout=30,
                       on_done=lambda r: fallo.append(r))
        time.sleep(1.5)
        assert not any(getattr(r, "success", False) for r in fallo), \
            "spawn_async ejecutó un subagente anidado"


def test_un_nivel_mas_hondo_tambien_se_niega():
    with entorno(7), tempfile.TemporaryDirectory() as tmp:
        try:
            SubAgentSpawner(work_dir=tmp).spawn(task="hola", agent="ADA", timeout=30)
        except SubAgentNestingError:
            return
        raise AssertionError("profundidad 7 debería negarse igual que 1")


# ── EL MECANISMO: que la marca cruce el proceso ───────────────────────────────

def test_la_marca_VIAJA_al_proceso_hijo():
    """EL TEST QUE PRUEBA EL CABLE.

    Lanza un worker REAL que imprime la marca que recibió. Si alguien borra el `env=`
    del `subprocess.run`, el hijo hereda la marca del padre (0 o ausente), se cree
    principal y puede abrir otro nivel — y todos los tests de arriba siguen verdes.
    """
    with entorno(None), tempfile.TemporaryDirectory() as tmp:
        worker = pathlib.Path(tmp) / "eco_de_profundidad.py"
        worker.write_text(
            "import os, sys\n"
            f"print(os.environ.get({_DEPTH_ENV!r}, 'AUSENTE'))\n"
        )
        r = SubAgentSpawner(work_dir=tmp).spawn(
            task="deci tu profundidad", agent="ADA",
            worker_script=str(worker), timeout=30)
        assert r.success, f"el worker no corrió: {r.error}"
        assert r.output.strip() == "1", (
            f"el hijo recibió {r.output.strip()!r}; la marca no viajó y el árbol queda abierto")


def test_el_entorno_del_hijo_no_pierde_el_resto():
    """Copiar sólo la marca dejaría al hijo sin PATH, sin venv y sin credenciales."""
    with entorno(None), tempfile.TemporaryDirectory() as tmp:
        env = SubAgentSpawner(work_dir=tmp)._entorno_del_hijo()
        assert env[_DEPTH_ENV] == "1"
        for clave in ("PATH", "HOME"):
            if clave in os.environ:
                assert env.get(clave) == os.environ[clave], f"se perdió {clave}"


def main() -> int:
    tests = [
        test_sin_marca_soy_el_agente_principal,
        test_con_marca_leo_mi_nivel,
        test_una_marca_ilegible_falla_CERRADO,
        test_un_negativo_no_reabre_el_permiso,
        test_el_principal_SI_puede_lanzar,
        test_un_subagente_NO_puede_lanzar,
        test_spawn_async_TAMBIEN_esta_cerrado,
        test_un_nivel_mas_hondo_tambien_se_niega,
        test_la_marca_VIAJA_al_proceso_hijo,
        test_el_entorno_del_hijo_no_pierde_el_resto,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"[OK] {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(main())
