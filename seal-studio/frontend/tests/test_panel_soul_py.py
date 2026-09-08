"""Wrapper pytest → node tests para PanelSoulLink / panelSoul.ts."""
import subprocess, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
_TEST = str(ROOT / "seal-studio/frontend/tests/panelSoul.test.mjs")
_NODE = ["node", "--experimental-transform-types", "--test"]


def _run(*extra_args):
    r = subprocess.run(
        _NODE + list(extra_args) + [_TEST],
        capture_output=True, text=True, cwd=ROOT,
    )
    return r


def test_qa_positive_solo_admin_y_superuser_ven_el_link():
    """Roles administrativos reciben True; roles ordinarios reciben False."""
    r = _run("--test-name-pattern", "Panel SOUL navigation")
    assert r.returncode == 0, f"node test failed:\n{r.stdout}\n{r.stderr}"
    assert "pass 1" in r.stdout


def test_qa_control_proxy_apunta_solo_al_loopback_8093():
    """El rewrite de /panel-soul apunta a 127.0.0.1:8093 y no altera /bridge ni /studio."""
    r = _run("--test-name-pattern", "Panel SOUL proxies")
    assert r.returncode == 0, f"node test failed:\n{r.stdout}\n{r.stderr}"
    assert "pass 1" in r.stdout


def test_qa_negative_usuario_comun_no_ve_el_link():
    """Roles no administrativos (user, viewer, '') reciben False y no ven el enlace."""
    r = _run("--test-name-pattern", "Panel SOUL navigation")
    assert r.returncode == 0, f"node test failed:\n{r.stdout}\n{r.stderr}"
    assert "fail 0" in r.stdout


def test_unit_suite_completa():
    """3/3 tests node pasan sin excepción.

    EL NÚMERO VA CLAVADO A PROPÓSITO, y conviene decirlo porque parece un
    descuido: `returncode == 0` también da verde si un test se deja de
    ejecutar —node no falla por no correr algo—, así que sin el conteo la
    suite podría encogerse en silencio. El precio es que agregar un brazo
    obliga a tocar esta línea; eso es intencional, no fricción accidental.

    Actualizado a 3 el 8-sep-2026 al sumar el brazo de renderizado del
    componente (NEXUS, carril nexus-panel-soul-coverage).
    """
    r = _run()
    assert r.returncode == 0, f"node test failed:\n{r.stdout}\n{r.stderr}"
    assert "# fail 0" in r.stdout, f"algun test de node fallo:\n{r.stdout}"
    assert "pass 3" in r.stdout, (
        "la suite de node no corrio los 3 brazos esperados; si agregaste uno, "
        f"actualiza este numero a proposito:\n{r.stdout}")
