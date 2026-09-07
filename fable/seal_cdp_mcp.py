#!/usr/bin/env python3
"""seal_cdp_mcp.py — MCP server nativo que expone el navegador SEAL a TODOS los agentes.

Orden de William 18-jul: *"ese MCP será el que usen TODOS ustedes SÍ O SÍ para navegar"* +
*"integra a todos los agentes"*. Este server envuelve `seal_cdp.CDP` (nuestro Chrome DevTools
nativo, cero Puppeteer/Node) como tools MCP. Reemplaza al `playwright` externo por el navegador
NUESTRO: los agentes navegan, ven, actúan y descargan con nuestra propia capa.

MCP es solo el TRANSPORTE agente→tool; la implementación sigue siendo nativa (Python + CDP).
La auditoría segura y el confinamiento de artefactos son obligatorios en esta frontera. Las
acciones sensibles migrarán al broker/approval nativo antes del cutover global.

Estado: una instancia de navegador por proceso-server (cada agente lanza el suyo → su navegador).
Registro en `.mcp.json`: el host llama por ``sudo -u seal-mcp-web-cdp`` al wrapper
root-owned ``/usr/local/libexec/seal/seal-cdp-mcp-wrapper``; nunca ejecuta esta
fuente mutable bajo el UID compartido.
(renombrado desde "seal-cdp" por orden de William 21-jul; el motor sigue siendo seal_cdp.py)
"""
import atexit
import grp
import inspect
import os
import pwd
import re
import shutil
import signal
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp.server.fastmcp import FastMCP
from mcp_web_soul_security import (
    AuditTrail,
    resolve_artifact_path,
    sanitize_mapping,
    sanitize_network_records,
    sanitize_url,
)
from mcp_web_soul_control import (
    APPROVAL_REQUIRED,
    BrowserControlPlane,
    ControlDenied,
    classify_action,
    discover_orphan_browsers,
)
from mcp_web_soul_visual import VisualTakeoverRuntime
from mcp_web_soul_profiles import ProfileVault
from seal_cdp import CDP

mcp = FastMCP("mcp-web-soul")

_browser = None
_MAX_TEXT = 8000  # recorte para no reventar el contexto del agente
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_AGENT = re.sub(r"[^A-Za-z0-9_.-]", "_", os.environ.get("SEAL_AGENT", "unknown"))[:64]
_STATE_ROOT = Path(
    os.environ.get("MCP_WEB_SOUL_STATE_ROOT", _PROJECT_ROOT / "var" / "mcp-web-soul")
).expanduser().resolve()
_CONTROL_ROOT = Path(
    os.environ.get("MCP_WEB_SOUL_CONTROL_ROOT", _STATE_ROOT)
).expanduser().resolve()
_CONTROL_GROUP = os.environ.get("MCP_WEB_SOUL_CONTROL_GROUP", "dadito").strip() or None
_READER_GROUP = os.environ.get("MCP_WEB_SOUL_READER_GROUP", "").strip() or None
_READER_GID = grp.getgrnam(_READER_GROUP).gr_gid if _READER_GROUP else None
_ARTIFACT_ROOT = _STATE_ROOT / "artifacts" / _AGENT
_AUDIT = AuditTrail(
    # Un stream por identidad: el witness mantiene una secuencia monotónica por
    # stream; compartir un único JSONL entre agentes rompía el primer append de
    # cada identidad nueva (su secuencia local no empezaba en 1).
    _STATE_ROOT / "audit" / f"events-{_AGENT}.jsonl",
    agent=_AGENT,
    witness_socket="/run/seal-audit-witness/witness.sock",
    witness_required=True,
    reader_group=_READER_GROUP,
)
_CDP_AUDIT_PATH = _STATE_ROOT / "audit" / f"cdp-{_AGENT}.jsonl"
_CONTROL = None
_SESSION_ID = None
_VISUAL = None
_PROFILE_DIR = None
_VAULT = None
_CUTOVER_BARRIER = Path("/run/seal-mcp-web-soul-cutover/legacy-disabled")


def _enforce_cutover_start_barrier() -> None:
    """Reject a shared-UID server before FastMCP enters its stdio loop."""

    if not _CUTOVER_BARRIER.exists():
        return
    try:
        dedicated_uid = pwd.getpwnam("seal-mcp-web-cdp").pw_uid
    except KeyError as exc:
        raise SystemExit("mcp-web-soul cutover is active without a dedicated identity") from exc
    if os.geteuid() != dedicated_uid:
        raise SystemExit("legacy shared-UID mcp-web-soul entrypoint is disabled")


def _control():
    global _CONTROL
    if _CONTROL is None:
        _CONTROL = BrowserControlPlane(
            _CONTROL_ROOT / "control.sqlite3",
            shared_group=_CONTROL_GROUP,
        )
    return _CONTROL


def _managed_artifact_path(requested: str) -> Path:
    path = resolve_artifact_path(requested, _ARTIFACT_ROOT)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    if _READER_GID is not None:
        current = path.parent
        while current == _ARTIFACT_ROOT or _ARTIFACT_ROOT in current.parents:
            os.chown(current, -1, _READER_GID)
            os.chmod(current, 0o2750)
            if current == _ARTIFACT_ROOT:
                break
            current = current.parent
    return path


def _publish_artifact(path: Path) -> None:
    if _READER_GID is not None:
        os.chown(path, -1, _READER_GID)
        os.chmod(path, 0o640)
    else:
        os.chmod(path, 0o600)


def _prepare_reader_file(path: Path) -> None:
    directory_mode = 0o2750 if _READER_GID is not None else 0o700
    file_mode = 0o640 if _READER_GID is not None else 0o600
    path.parent.mkdir(parents=True, exist_ok=True, mode=directory_mode)
    if _READER_GID is not None:
        os.chown(path.parent, -1, _READER_GID)
    os.chmod(path.parent, directory_mode)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, file_mode)
    os.close(fd)
    if _READER_GID is not None:
        os.chown(path, -1, _READER_GID)
    os.chmod(path, file_mode)


def _state_access_ok() -> bool:
    """Validate the intended group boundaries without requiring state to exist."""

    checks: list[bool] = []
    if _AUDIT.path.exists():
        mode = _AUDIT.path.stat().st_mode & 0o777
        checks.append(mode == (0o640 if _READER_GID is not None else 0o600))
        if _READER_GID is not None:
            checks.append(_AUDIT.path.stat().st_gid == _READER_GID)
    for path in (_control().db_path, _control().key_path):
        if not path.exists():
            continue
        checks.append((path.stat().st_mode & 0o007) == 0)
        if _CONTROL_GROUP:
            checks.append(path.stat().st_gid == grp.getgrnam(_CONTROL_GROUP).gr_gid)
            checks.append((path.stat().st_mode & 0o770) == 0o660)
        else:
            checks.append((path.stat().st_mode & 0o777) == 0o600)
    return all(checks)


def _session_id():
    global _SESSION_ID
    if _SESSION_ID is None:
        _SESSION_ID = _control().ensure_session(agent=_AGENT, pid=os.getpid())
    else:
        _control().heartbeat(_SESSION_ID)
    return _SESSION_ID


def _visual():
    global _VISUAL
    if _VISUAL is None:
        _VISUAL = VisualTakeoverRuntime()
    return _VISUAL


def _human_credential_broker_enabled() -> bool:
    """Fail-closed hasta que el visor corra bajo una identidad OS separada.

    Una variable de entorno no es una atestación: cualquier proceso del UID
    compartido puede fijarla. Mientras agentes y operador compartan UID, habilitar
    Xauthority/VNC permitiría leer las credenciales desde otro proceso del mismo
    usuario. El navegador headless sigue operativo; el takeover humano permanece
    deliberadamente inaccesible.
    """

    return False


def _vault():
    global _VAULT
    if _VAULT is None:
        _VAULT = ProfileVault(_STATE_ROOT / "profiles" / _AGENT)
    return _VAULT


def _close_browser_process(*, delete_profile: bool, raise_errors: bool = False) -> None:
    global _browser, _VISUAL, _PROFILE_DIR
    try:
        if _browser is not None:
            _browser.close()
    except Exception:
        if raise_errors:
            raise
    finally:
        _browser = None
        if _VISUAL is not None:
            try:
                _VISUAL.close()
            except Exception:
                if raise_errors:
                    raise
            finally:
                _VISUAL = None
        if delete_profile and _PROFILE_DIR is not None:
            shutil.rmtree(_PROFILE_DIR, ignore_errors=True)
            _PROFILE_DIR = None


def _shutdown_browser(*, raise_errors=False):
    """Cierra la instancia propiedad de este servidor MCP.

    Un cliente MCP puede desaparecer sin alcanzar a invocar ``close_browser`` (reinicio del
    agente, EOF de stdio o SIGTERM del host). Brave corre en una sesión propia para poder matar
    todo su árbol; por eso, si el servidor muere sin esta salida, el navegador sobrevive como
    huérfano. Este hook vuelve el teardown parte del ciclo de vida del servidor, no una cortesía
    que dependa del agente.
    """
    global _SESSION_ID
    try:
        _close_browser_process(delete_profile=True, raise_errors=raise_errors)
    finally:
        if _SESSION_ID is not None:
            try:
                _control().close_session(_SESSION_ID)
            except Exception:
                if raise_errors:
                    raise
            finally:
                _SESSION_ID = None


def _handle_shutdown_signal(signum, _frame):
    """Teardown acotado ante el SIGTERM que usan los hosts MCP al cerrar stdio."""
    _shutdown_browser(raise_errors=False)
    raise SystemExit(128 + signum)


def _get():
    """Instancia perezosa con auditoría del motor habilitada siempre."""
    global _browser, _PROFILE_DIR
    if _browser is None:
        _prepare_reader_file(_CDP_AUDIT_PATH)
        if _PROFILE_DIR is None:
            runtime_root = _STATE_ROOT / "runtime-profiles" / _AGENT
            runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            _PROFILE_DIR = tempfile.mkdtemp(prefix="profile-", dir=runtime_root)
            os.chmod(_PROFILE_DIR, 0o700)
        visual_enabled = _human_credential_broker_enabled()
        _browser = CDP(
            headless=not visual_enabled,
            audit_path=str(_CDP_AUDIT_PATH),
            purpose="mcp-web-soul",
            deny_private_networks=True,
            process_env=_visual().browser_env if visual_enabled else None,
            user_data_dir=_PROFILE_DIR,
        )
    return _browser


def _clip(s):
    s = s or ""
    return s if len(s) <= _MAX_TEXT else s[:_MAX_TEXT] + f"\n…[recortado, {len(s)} chars total]"


def _risk_context(tool: str, arguments: dict) -> dict:
    """Deriva riesgo del DOM vivo sin alterar el hash de los argumentos solicitados."""
    tool = str(tool).strip().lower()
    selector = ""
    delegated = ""
    if tool == "browser_action":
        delegated = str(arguments.get("action", "")).strip().lower()
    if tool in {"click", "type_text"}:
        selector = str(arguments.get("selector", ""))
    elif tool == "browser_action" and delegated in {"click", "type_text"}:
        selector = str(arguments.get("target", ""))
    if not selector:
        return {}
    expression = f"""
    (() => {{
      const e = document.querySelector({selector!r});
      if (!e) return {{found:false}};
      const form = e.closest ? e.closest('form') : null;
      return {{
        found: true,
        tag: String(e.tagName || '').slice(0,32),
        type: String(e.type || '').slice(0,64),
        autocomplete: String(e.getAttribute && e.getAttribute('autocomplete') || '').slice(0,64),
        role: String(e.getAttribute && e.getAttribute('role') || '').slice(0,64),
        ariaLabel: String(e.getAttribute && e.getAttribute('aria-label') || '').slice(0,160),
        text: String(e.innerText || e.textContent || e.value || '').trim().slice(0,200),
        href: String(e.href || '').slice(0,500),
        formAction: String((e.form && e.form.action) || (form && form.action) || '').slice(0,500),
        formMethod: String((e.form && e.form.method) || (form && form.method) || '').slice(0,16),
        submitsForm: Boolean(
          (String(e.tagName || '').toLowerCase() === 'button' && (!e.type || e.type === 'submit')) ||
          (String(e.tagName || '').toLowerCase() === 'input' && e.type === 'submit')
        )
      }};
    }})()
    """
    try:
        context = _get().eval_js(expression)
    except Exception:
        # Fallar conservadoramente: un click opaco puede confirmar una mutación;
        # un type_text opaco puede ser una credencial.
        return (
            {"inspection_failed": True, "submit": True}
            if tool == "click" or delegated == "click"
            else {"inspection_failed": True, "type": "password"}
        )
    if not isinstance(context, dict):
        return (
            {"inspection_failed": True, "submit": True}
            if tool == "click" or delegated == "click"
            else {"inspection_failed": True, "type": "password"}
        )
    return sanitize_mapping(context, tool=delegated or tool)


def _audited(fn):
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            arguments = dict(inspect.signature(fn).bind_partial(*args, **kwargs).arguments)
        except (TypeError, ValueError):
            arguments = {"args": list(args), "kwargs": kwargs}
        control_arguments = dict(arguments)
        approval_id = str(control_arguments.pop("approval_id", "") or "")
        risk_context = _risk_context(fn.__name__, control_arguments)
        action_class = classify_action(
            fn.__name__, control_arguments, risk_context=risk_context
        )
        session_id = _session_id()
        permit = None
        operation_id = None
        try:
            permit = _control().authorize(
                session_id=session_id,
                tool=fn.__name__,
                arguments=control_arguments,
                approval_id=approval_id or None,
                risk_context=risk_context,
                arm_remote_effect=True,
            )
            operation_id = permit.approval_id
            _AUDIT.append(
                tool=f"{fn.__name__}.intent",
                arguments=control_arguments,
                ok=True,
                session_id=session_id,
                action_class=action_class,
                required=action_class in APPROVAL_REQUIRED,
            )
        except ControlDenied as exc:
            _AUDIT.append(
                tool=f"{fn.__name__}.denied",
                arguments=control_arguments,
                ok=False,
                session_id=session_id,
                action_class=action_class,
                error=str(exc),
            )
            return {
                "ok": False,
                "error": str(exc),
                "error_code": exc.code,
                "http_status": 423 if exc.code == "takeover_locked" else 403,
            }
        except Exception as exc:
            if permit is not None:
                _control().mark_remote_effect_indeterminate(
                    permit,
                    operation_id,
                    error_code=f"intent_audit:{type(exc).__name__}",
                )
            raise
        try:
            result = fn(*args, **kwargs)
            dict_error = isinstance(result, dict) and result.get("ok") is False
            text_error = isinstance(result, str) and result.startswith("[error:")
            ok = not (dict_error or text_error)
            if dict_error:
                error = str(result.get("error", "tool returned ok=false"))
            elif text_error:
                error = result
            else:
                error = None
            error_code = "tool_failed" if not ok else None
            # For an approval-bound remote action, ``ok=false`` is not proof
            # that no effect occurred. A click/navigation may commit remotely
            # and only then time out or fail while reading the response. Keep
            # the global effect fence armed until an external operator
            # reconciles it; never silently make a retry possible.
            if not ok and operation_id is not None:
                _control().mark_remote_effect_indeterminate(
                    permit,
                    operation_id,
                    error_code=error_code or "tool_returned_failure",
                )
                _AUDIT.append(
                    tool=fn.__name__,
                    arguments=control_arguments,
                    ok=False,
                    session_id=session_id,
                    action_class=action_class,
                    error=error,
                    required=True,
                )
                return result
            _control().observe_remote_effect(operation_id, ok=ok, error_code=error_code)
            try:
                _AUDIT.append(
                    tool=fn.__name__,
                    arguments=control_arguments,
                    ok=ok,
                    session_id=session_id,
                    action_class=action_class,
                    error=error,
                    required=action_class in APPROVAL_REQUIRED,
                )
            except Exception as exc:
                _control().mark_remote_effect_indeterminate(
                    permit,
                    operation_id,
                    error_code=f"completion_audit:{type(exc).__name__}",
                )
                raise
            _control().finalize_remote_effect(
                permit,
                operation_id,
                ok=ok,
                error_code=error_code,
            )
            return result
        except Exception as exc:
            current = _control().remote_effect_operation(operation_id) if operation_id else None
            if current is None or current.get("status") not in {"completed", "indeterminate"}:
                _control().mark_remote_effect_indeterminate(
                    permit,
                    operation_id,
                    error_code=type(exc).__name__,
                )
            try:
                _AUDIT.append(
                    tool=fn.__name__,
                    arguments=control_arguments,
                    ok=False,
                    session_id=session_id,
                    action_class=action_class,
                    error=str(exc),
                    required=action_class in APPROVAL_REQUIRED,
                )
            except Exception:
                pass
            raise
    return wrapper


@mcp.tool()
@_audited
def browse(url: str, wait_until: str = "load", approval_id: str = "") -> dict:
    """Navega a `url` y devuelve lo que VE la página: status, título y texto visible.
    wait_until: load | domcontentloaded | networkidle | none. Es el 'ver una página' del agente."""
    try:
        b = _get()
        b.navigate(url, wait_until=wait_until)
        final_url = str(b.eval_js("location.href"))
        reqs = [
            r for r in b.network_requests()
            if r.get("url", "").split("#")[0] == final_url.split("#")[0]
        ]
        status = reqs[-1].get("status") if reqs else None
        return {"ok": True, "requested_url": url, "url": final_url, "status": status,
                "title": b.eval_js("document.title"), "text": _clip(b.text())}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
@_audited
def get_text() -> str:
    """Texto visible (innerText) de la página actual."""
    try:
        return _clip(_get().text())
    except Exception as exc:
        return f"[error: {exc}]"


@mcp.tool()
@_audited
def get_html() -> str:
    """HTML renderizado (outerHTML) de la página actual."""
    try:
        return _clip(_get().html())
    except Exception as exc:
        return f"[error: {exc}]"


@mcp.tool()
@_audited
def network() -> dict:
    """Requests de la última navegación: url, status, mime, si viajó cookie, CORS. Para diagnóstico.
    Devuelve {requests: [...], count: N} — un dict (no lista pelada) para que llegue como UN
    bloque JSON limpio al agente vía MCP (una lista pelada se serializa como N bloques sueltos)."""
    try:
        reqs = sanitize_network_records(_get().network_requests(with_headers=True))
        return {"requests": reqs, "count": len(reqs)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "requests": [], "count": 0}


@mcp.tool()
@_audited
def click(selector: str, approval_id: str = "") -> dict:
    """Click nativo (dispara handlers reales) en el elemento CSS `selector`."""
    try:
        _get().click(selector)
        return {"ok": True, "clicked": selector}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
@_audited
def type_text(selector: str, value: str, approval_id: str = "") -> dict:
    """Enfoca `selector` y teclea `value` (input real, dispara eventos de React/Vue)."""
    try:
        _get().type_text(selector, value)
        return {"ok": True, "typed_into": selector}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
@_audited
def wait_for(selector: str, timeout: float = 10.0) -> dict:
    """Espera hasta que `selector` exista (o venza `timeout`). Para contenido dinámico."""
    try:
        return {"ok": True, "found": _get().wait_for(selector, timeout=timeout)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
@_audited
def set_cookie(name: str, value: str, url: str, approval_id: str = "") -> dict:
    """Inyecta una cookie (p. ej. la sesión) para reproducir flujos AUTENTICADOS antes de save_url."""
    try:
        return {"ok": bool(_get().set_cookie(name, value, url=url))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
@_audited
def save_url(url: str, path: str, approval_id: str = "") -> dict:
    """DESCARGA `url` a una ruta relativa bajo el directorio gestionado del agente.
    Las 'manos' del agente para bajar archivos. Devuelve {status, bytes, path}."""
    try:
        managed_path = _managed_artifact_path(path)
        result = _get().save_url(url, str(managed_path))
        _publish_artifact(managed_path)
        return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
@_audited
def screenshot(path: str, full_page: bool = False) -> dict:
    """Captura PNG bajo el directorio gestionado (full_page=True = toda la altura)."""
    try:
        managed_path = _managed_artifact_path(path)
        _get().screenshot(str(managed_path), full_page=full_page)
        _publish_artifact(managed_path)
        return {"ok": True, "path": str(managed_path), "bytes": managed_path.stat().st_size}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
@_audited
def browser_session(operation: str = "status") -> dict:
    """Consulta/renueva la sesión supervisada del proceso MCP actual."""
    session_id = _session_id()
    operation = str(operation).strip().lower()
    if operation == "heartbeat":
        _control().heartbeat(session_id)
    elif operation != "status":
        return {"ok": False, "error": "operation debe ser status|heartbeat"}
    return {"ok": True, **_control().session_status(session_id)}


@mcp.tool()
@_audited
def browser_approval(
    operation: str,
    approval_id: str = "",
    tool: str = "",
    arguments: dict | None = None,
    ttl_seconds: int = 300,
) -> dict:
    """Solicita o consulta approval; aprobar requiere identidad externa autenticada."""
    operation = str(operation).strip().lower()
    if operation == "request":
        try:
            requested = _control().request_approval(
                session_id=_session_id(),
                tool=tool,
                arguments=arguments or {},
                ttl_seconds=ttl_seconds,
                risk_context=_risk_context(tool, arguments or {}),
            )
            return {
                "ok": True,
                **requested,
                "operator_command": f"OK BROWSER APPROVE {requested['approval_id']}",
            }
        except ControlDenied as exc:
            return {"ok": False, "error": str(exc), "error_code": exc.code}
    if operation == "status":
        try:
            return {"ok": True, **_control().approval_status(approval_id)}
        except ControlDenied as exc:
            return {"ok": False, "error": str(exc), "error_code": exc.code}
    return {"ok": False, "error": "operation debe ser request|status"}


@mcp.tool()
@_audited
def browser_takeover(operation: str = "status") -> dict:
    """Solicita takeover o consulta su estado; el agente no puede auto-activarlo/liberarlo."""
    operation = str(operation).strip().lower()
    if not _human_credential_broker_enabled():
        return {
            "ok": False,
            "error_code": "human_credential_delivery_unavailable",
            "error": "takeover bloqueado: falta broker operator-only para entregar credenciales",
        }
    try:
        if operation == "request":
            _get()
            requested = _control().request_takeover(_session_id())
            return {
                "ok": True,
                **requested,
                "takeover_url": None,
                "exposure": "loopback-authenticated",
                "operator_command": f"OK BROWSER TAKEOVER {requested['session_id']}",
            }
        if operation == "status":
            status = _control().session_status(_session_id())
            if status["state"] == "ready" and _VISUAL is not None:
                _VISUAL.stop_viewer()
            response = {
                "ok": True,
                **status,
                "visual": _VISUAL.status() if _VISUAL is not None else {
                    "visual_session": False,
                    "viewer_active": False,
                    "takeover_url": None,
                    "exposure": "loopback-authenticated",
                },
            }
            if status["state"] == "human_active":
                response["release_command"] = f"OK BROWSER RELEASE {status['session_id']}"
                response["renew_command"] = f"OK BROWSER RENEW {status['session_id']}"
            return response
        return {"ok": False, "error": "operation debe ser request|status"}
    except ControlDenied as exc:
        return {"ok": False, "error": str(exc), "error_code": exc.code}


@mcp.tool()
@_audited
def browser_profile(operation: str, name: str = "", approval_id: str = "") -> dict:
    """Lista, guarda o carga un perfil cifrado; save/load requieren approval."""
    global _PROFILE_DIR
    operation = str(operation).strip().lower()
    try:
        if operation == "list":
            profiles = _vault().list()
            return {"ok": True, "profiles": profiles, "count": len(profiles)}
        if operation == "save":
            if _browser is None or _PROFILE_DIR is None:
                return {"ok": False, "error": "no hay sesión de navegador para guardar"}
            profile_dir = _PROFILE_DIR
            _close_browser_process(delete_profile=False, raise_errors=True)
            saved = _vault().save(name, profile_dir)
            shutil.rmtree(profile_dir, ignore_errors=True)
            _PROFILE_DIR = None
            return {"ok": True, **saved, "state": "encrypted_at_rest"}
        if operation == "load":
            _close_browser_process(delete_profile=True, raise_errors=True)
            runtime_root = _STATE_ROOT / "runtime-profiles" / _AGENT
            runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            _PROFILE_DIR = tempfile.mkdtemp(prefix="profile-", dir=runtime_root)
            os.chmod(_PROFILE_DIR, 0o700)
            loaded = _vault().load(name, _PROFILE_DIR)
            return {"ok": True, **loaded, "state": "loaded_for_next_browse"}
        return {"ok": False, "error": "operation debe ser list|save|load"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
@_audited
def browser_readiness() -> dict:
    """Readiness del control plane: sesión, audit chain y archivos privados."""
    audit_ok, events = _AUDIT.verify()
    private = _state_access_ok()
    session = _control().session_status(_session_id())
    orphans = discover_orphan_browsers()
    ok = audit_ok and private and session["state"] == "ready" and not orphans
    return {
        "ok": ok,
        "overall": "pass" if ok else "fail",
        "session": session,
        "audit_chain": {"ok": audit_ok, "events": events},
        "private_state": private,
        "orphan_browsers": orphans,
        "visual": _VISUAL.status() if _VISUAL is not None else {
            "visual_session": False,
            "viewer_active": False,
            "takeover_url": None,
            "exposure": "loopback-authenticated",
        },
    }


@mcp.tool()
@_audited
def browser_observe(
    observation: str,
    selector: str = "",
    path: str = "",
    full_page: bool = False,
    max_nodes: int = 200,
) -> dict:
    """Observación agrupada: text|html|query|network|console|tabs|accessibility|performance|screenshot."""
    observation = str(observation).strip().lower()
    b = _get()
    try:
        if observation == "text":
            return {"ok": True, "text": _clip(b.text())}
        if observation == "html":
            return {"ok": True, "html": _clip(b.html())}
        if observation == "query":
            if not selector:
                return {"ok": False, "error": "selector requerido"}
            return {"ok": True, "selector": selector, "value": _clip(b.query(selector))}
        if observation == "network":
            requests = sanitize_network_records(b.network_requests(with_headers=True))
            return {"ok": True, "requests": requests, "count": len(requests)}
        if observation == "console":
            return {"ok": True, **sanitize_mapping({"events": b.console()}, tool="browser_observe")}
        if observation == "tabs":
            tabs = []
            for tab in b.tabs():
                safe = dict(tab)
                if "url" in safe:
                    safe["url"] = sanitize_url(safe["url"])
                tabs.append(sanitize_mapping(safe, tool="browser_observe"))
            return {"ok": True, "tabs": tabs, "count": len(tabs)}
        if observation == "accessibility":
            return {"ok": True, "tree": b.accessibility_tree(max_nodes=max(1, min(max_nodes, 500)))}
        if observation == "performance":
            return {"ok": True, "metrics": b.performance_metrics()}
        if observation == "screenshot":
            if not path:
                path = f"screens/{int(__import__('time').time())}.png"
            managed_path = _managed_artifact_path(path)
            b.screenshot(str(managed_path), full_page=full_page)
            _publish_artifact(managed_path)
            return {"ok": True, "path": str(managed_path), "bytes": managed_path.stat().st_size}
        return {"ok": False, "error": "observation no soportada"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
@_audited
def browser_action(
    action: str,
    target: str = "",
    value: str = "",
    url: str = "",
    timeout: float = 10.0,
    approval_id: str = "",
) -> dict:
    """Acción agrupada de navegación/formulario.

    Soporta navigate, back, click, hover, drag (target->value), type_text,
    select_option, press_key, upload_file, dialog_accept/dialog_dismiss,
    resize (target=ANCHOxALTO), waits, cookies y pestañas.
    """
    action = str(action).strip().lower()
    b = _get()
    try:
        if action == "navigate":
            if not url:
                return {"ok": False, "error": "url requerida"}
            b.navigate(url, wait_until="load", timeout=timeout)
            final_url = str(b.eval_js("location.href"))
            return {"ok": True, "url": final_url, "title": b.eval_js("document.title")}
        if action == "click":
            b.click(target)
            return {"ok": True, "clicked": target}
        if action == "hover":
            b.hover(target)
            return {"ok": True, "hovered": target}
        if action == "drag":
            b.drag(target, value)
            return {"ok": True, "source": target, "destination": value}
        if action == "type_text":
            b.type_text(target, value)
            return {"ok": True, "typed_into": target}
        if action == "select_option":
            b.select_option(target, value)
            return {"ok": True, "selected": value, "target": target}
        if action == "press_key":
            b.press_key(value or target)
            return {"ok": True, "key": value or target}
        if action == "upload_file":
            managed_path = resolve_artifact_path(value, _ARTIFACT_ROOT)
            if not managed_path.is_file():
                return {"ok": False, "error": "archivo gestionado inexistente"}
            b.upload_file(target, str(managed_path))
            return {"ok": True, "target": target, "path": str(managed_path)}
        if action in {"dialog_accept", "dialog_dismiss"}:
            b.handle_dialog(action == "dialog_accept", value)
            return {"ok": True, "accepted": action == "dialog_accept"}
        if action == "back":
            return {"ok": True, "navigated": bool(b.navigate_back())}
        if action == "resize":
            match = re.fullmatch(r"(\d{2,5})x(\d{2,5})", target.strip().lower())
            if not match:
                return {"ok": False, "error": "target debe ser ANCHOxALTO"}
            width, height = map(int, match.groups())
            if not (240 <= width <= 7680 and 240 <= height <= 4320):
                return {"ok": False, "error": "viewport fuera de límites"}
            b.emulate_device(width, height)
            return {"ok": True, "width": width, "height": height}
        if action == "wait_for":
            return {"ok": True, "found": b.wait_for(target, timeout=timeout)}
        if action == "wait_for_text":
            return {"ok": True, "found": b.wait_for_text(target, timeout=timeout)}
        if action == "set_cookie":
            return {"ok": bool(b.set_cookie(target, value, url=url))}
        if action == "open_tab":
            return {"ok": True, "target_id": b.open_tab(url or "about:blank")}
        if action == "switch_tab":
            b.switch_tab(target)
            return {"ok": True, "target_id": target}
        return {"ok": False, "error": "action no soportada"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
@_audited
def browser_trace(
    operation: str,
    path: str = "traces/browser-trace.json",
    screenshots: bool = False,
) -> dict:
    """Inicia o detiene tracing CDP; el resultado queda confinado como artifact."""
    operation = str(operation).strip().lower()
    try:
        if operation == "start":
            _get().start_trace(screenshots=screenshots)
            return {"ok": True, "state": "recording"}
        if operation == "stop":
            managed_path = _managed_artifact_path(path)
            result = _get().stop_trace(str(managed_path))
            _publish_artifact(managed_path)
            return {"ok": True, "path": str(managed_path), "result": result}
        return {"ok": False, "error": "operation debe ser start|stop"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
@_audited
def close_browser() -> dict:
    """Cierra el navegador (libera Brave + el profile). Se relanza solo en el próximo browse()."""
    try:
        _shutdown_browser(raise_errors=True)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _run_server() -> None:
    # FastMCP/stdio termina normalmente por EOF o SIGTERM. Cubrimos ambos caminos: atexit
    # para salida normal y señales para que el host no deje Brave/profile huérfanos.
    _enforce_cutover_start_barrier()
    atexit.register(_shutdown_browser)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    mcp.run()


if __name__ == "__main__":
    _run_server()
