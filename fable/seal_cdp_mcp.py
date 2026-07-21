#!/usr/bin/env python3
"""seal_cdp_mcp.py — MCP server nativo que expone el navegador SEAL a TODOS los agentes.

Orden de William 18-jul: *"ese MCP será el que usen TODOS ustedes SÍ O SÍ para navegar"* +
*"integra a todos los agentes"*. Este server envuelve `seal_cdp.CDP` (nuestro Chrome DevTools
nativo, cero Puppeteer/Node) como tools MCP. Reemplaza al `playwright` externo por el navegador
NUESTRO: los agentes navegan, ven, actúan y descargan con nuestra propia capa.

MCP es solo el TRANSPORTE agente→tool; la implementación sigue siendo nativa (Python + CDP).
Enabling por defecto (ver + actuar + descargar). La gobernanza (allowlist/read-only/audit) NO se
expone acá — es opt-in gateado por NEXUS en su propio adapter.

Estado: una instancia de navegador por proceso-server (cada agente lanza el suyo → su navegador).
Registro en `.mcp.json`:  "seal-cdp": {"command": "python3", "args": [".../fable/seal_cdp_mcp.py"]}
"""
import atexit
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp.server.fastmcp import FastMCP
from seal_cdp import CDP

mcp = FastMCP("seal-cdp")

_browser = None
_MAX_TEXT = 8000  # recorte para no reventar el contexto del agente


def _shutdown_browser(*, raise_errors=False):
    """Cierra la instancia propiedad de este servidor MCP.

    Un cliente MCP puede desaparecer sin alcanzar a invocar ``close_browser`` (reinicio del
    agente, EOF de stdio o SIGTERM del host). Brave corre en una sesión propia para poder matar
    todo su árbol; por eso, si el servidor muere sin esta salida, el navegador sobrevive como
    huérfano. Este hook vuelve el teardown parte del ciclo de vida del servidor, no una cortesía
    que dependa del agente.
    """
    global _browser
    browser = _browser
    if browser is None:
        return
    try:
        browser.close()
    except Exception:
        if raise_errors:
            raise
    finally:
        # close() es idempotente. Quitar la referencia evita que atexit repita trabajo tras
        # un cierre explícito o después del handler de señal.
        _browser = None


def _handle_shutdown_signal(signum, _frame):
    """Teardown acotado ante el SIGTERM que usan los hosts MCP al cerrar stdio."""
    _shutdown_browser(raise_errors=False)
    raise SystemExit(128 + signum)


def _get():
    """Instancia perezosa del navegador (enabling por defecto)."""
    global _browser
    if _browser is None:
        _browser = CDP(headless=True)
    return _browser


def _clip(s):
    s = s or ""
    return s if len(s) <= _MAX_TEXT else s[:_MAX_TEXT] + f"\n…[recortado, {len(s)} chars total]"


@mcp.tool()
def browse(url: str, wait_until: str = "load") -> dict:
    """Navega a `url` y devuelve lo que VE la página: status, título y texto visible.
    wait_until: load | domcontentloaded | networkidle | none. Es el 'ver una página' del agente."""
    try:
        b = _get()
        b.navigate(url, wait_until=wait_until)
        reqs = [r for r in b.network_requests() if r.get("url", "").split("#")[0] == url]
        status = reqs[0]["status"] if reqs else None
        return {"ok": True, "url": url, "status": status,
                "title": b.eval_js("document.title"), "text": _clip(b.text())}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def get_text() -> str:
    """Texto visible (innerText) de la página actual."""
    try:
        return _clip(_get().text())
    except Exception as exc:
        return f"[error: {exc}]"


@mcp.tool()
def get_html() -> str:
    """HTML renderizado (outerHTML) de la página actual."""
    try:
        return _clip(_get().html())
    except Exception as exc:
        return f"[error: {exc}]"


@mcp.tool()
def network() -> dict:
    """Requests de la última navegación: url, status, mime, si viajó cookie, CORS. Para diagnóstico.
    Devuelve {requests: [...], count: N} — un dict (no lista pelada) para que llegue como UN
    bloque JSON limpio al agente vía MCP (una lista pelada se serializa como N bloques sueltos)."""
    try:
        reqs = _get().network_requests(with_headers=True)
        return {"requests": reqs, "count": len(reqs)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "requests": [], "count": 0}


@mcp.tool()
def click(selector: str) -> dict:
    """Click nativo (dispara handlers reales) en el elemento CSS `selector`."""
    try:
        _get().click(selector)
        return {"ok": True, "clicked": selector}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def type_text(selector: str, value: str) -> dict:
    """Enfoca `selector` y teclea `value` (input real, dispara eventos de React/Vue)."""
    try:
        _get().type_text(selector, value)
        return {"ok": True, "typed_into": selector}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def wait_for(selector: str, timeout: float = 10.0) -> dict:
    """Espera hasta que `selector` exista (o venza `timeout`). Para contenido dinámico."""
    try:
        return {"ok": True, "found": _get().wait_for(selector, timeout=timeout)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def set_cookie(name: str, value: str, url: str) -> dict:
    """Inyecta una cookie (p. ej. la sesión) para reproducir flujos AUTENTICADOS antes de save_url."""
    try:
        return {"ok": bool(_get().set_cookie(name, value, url=url))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def save_url(url: str, path: str) -> dict:
    """DESCARGA `url` a `path` con la sesión del navegador (recursos autenticados por efecto).
    Las 'manos' del agente para bajar archivos. Devuelve {status, bytes, path}."""
    try:
        return {"ok": True, **_get().save_url(url, path)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def screenshot(path: str, full_page: bool = False) -> dict:
    """Captura PNG de la página actual a `path` (full_page=True = toda la altura)."""
    try:
        _get().screenshot(path, full_page=full_page)
        return {"ok": True, "path": path, "bytes": os.path.getsize(path)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def close_browser() -> dict:
    """Cierra el navegador (libera Brave + el profile). Se relanza solo en el próximo browse()."""
    try:
        _shutdown_browser(raise_errors=True)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    # FastMCP/stdio termina normalmente por EOF o SIGTERM. Cubrimos ambos caminos: atexit
    # para salida normal y señales para que el host no deje Brave/profile huérfanos.
    atexit.register(_shutdown_browser)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    mcp.run()
