#!/usr/bin/env python3
"""
test_seal_cdp.py — Suite de pruebas REALES (por efecto) de la capa CDP nativa.

Orden de William 18-jul: "reescrito, nativo, EN PRODUCCIÓN, con tests/pruebas reales".
No mockea nada: lanza Brave headless de verdad, navega, inyecta cookies, teclea, clickea,
saca screenshot, y contra el chat server real prueba el flujo autenticado 401->200.

Uso:  python3 fable/test_seal_cdp.py
Sale con código 0 solo si TODAS las fases pasan.
"""
import os
import sys
import shutil
import asyncio
import json
import secrets
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _fresh_profile(path):
    """Profile de navegador limpio garantizado: sin pollution de cookies de runs previos
    (un baseline 'sin sesión' contaminado por una cookie persistida daría falso 200/401)."""
    shutil.rmtree(path, ignore_errors=True)
    return path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "messages"))
from seal_cdp import CDP  # noqa: E402

DSN = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".db_cred")).read().splitlines()[0].strip()  # lee de .db_cred, no credencial en literal
SERVER = "http://localhost:8765"
PROTECTED = f"{SERVER}/api/auth/me"  # contrato estable: anon=401, cookie real=200

PAGE = (
    "data:text/html,"
    "<html><head><title>CDP</title></head><body>"
    "<h1 id='h'>Hello CDP</h1>"
    "<input id='name'>"
    "<button id='btn' onclick=\"document.getElementById('out').innerText="
    "'clicked:'+document.getElementById('name').value\">Go</button>"
    "<div id='out'></div></body></html>"
)

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  ({detail})" if detail else ""))


def _profile_processes():
    """PIDs Brave/Chromium que aún referencian profiles de esta suite.

    No basta buscar el path en todo cmdline: un shell/heredoc que contiene el código de
    esta prueba también incluye la cadena y antes producía un falso zombie intermitente.
    """
    found = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            raw = open(f"/proc/{entry.name}/cmdline", "rb").read()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        argv = [part for part in raw.split(b"\0") if part]
        launch_names = [os.path.basename(part.decode(errors="ignore")).lower()
                        for part in argv[:2]]
        is_browser = any("brave" in name or "chromium" in name or name == "chrome-sandbox"
                         for name in launch_names)
        if is_browser and b"/tmp/seal_cdp_test_" in raw:
            found.append(int(entry.name))
    return found


class _DeterministicHandler(BaseHTTPRequestHandler):
    slow_hits = 0
    foreign_hits = 0
    post_hits = 0

    def log_message(self, *_args):
        pass

    def _write_if_connected(self, payload):
        """El navegador puede cancelar una request al bloquear/navegar; no ensuciar CI."""
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path == "/slow":
            type(self).slow_hits += 1
            time.sleep(0.35)
            payload = b'{"ok":true}'
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload))); self.end_headers()
            self._write_if_connected(payload); return
        if self.path == "/foreign":
            type(self).foreign_hits += 1
            payload = b"foreign"
            self.send_response(200); self.send_header("Content-Length", str(len(payload)))
            self.end_headers(); self._write_if_connected(payload); return
        payload = ("<html><body><h1>network</h1><div id='done'></div>"
                   "<script>fetch('/slow').then(()=>done.textContent='done')</script>"
                   "</body></html>").encode()
        self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(payload))); self.end_headers()
        self._write_if_connected(payload)

    def do_POST(self):
        type(self).post_hits += 1
        payload = b"posted"
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self._write_if_connected(payload)


def _server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DeterministicHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def phase_1_2_dom_input():
    """Fases 1 (navegar), 4 (DOM/screenshot), 5 (click/type) contra página determinista."""
    print("\n[Fases 1/4/5] navegación + DOM + input (página data:)")
    b = CDP(headless=True, user_data_dir=_fresh_profile("/tmp/seal_cdp_test_dom"))
    try:
        b.navigate(PAGE, settle=1.0)
        check("eval_js aritmética", b.eval_js("6*7") == 42)
        check("text() lee innerText", "Hello CDP" in (b.text() or ""))
        check("html() trae outerHTML", "<button" in (b.html() or "").lower())
        check("query selector existente", (b.query("#h") or "").startswith("Hello"))
        check("query selector ausente = None", b.query("#nope") is None)
        # Fase 5: type + click disparan handlers reales
        b.type_text("#name", "FABLE")
        b.click("#btn")
        out = b.query("#out")
        check("type_text + click end-to-end", out == "clicked:FABLE", f"out={out!r}")
        # screenshot real a archivo
        shot = "/tmp/seal_cdp_test_shot.png"
        if os.path.exists(shot):
            os.remove(shot)
        b.screenshot(shot)
        ok = os.path.exists(shot) and os.path.getsize(shot) > 1000
        with open(shot, "rb") as f:
            magic = f.read(8)
        check("screenshot PNG válido", ok and magic.startswith(b"\x89PNG"),
              f"{os.path.getsize(shot) if os.path.exists(shot) else 0} bytes")
    finally:
        b.close()


def phase_3_network_auth():
    """Fases 2 (headers/cookie) + 3 (set_cookie) + E2E auth real (401->200) contra server real."""
    print("\n[Fases 2/3] red + cookies + flujo autenticado real (chat server)")
    test_user = f"fable_cdp_e2e_{os.getpid()}_{secrets.token_hex(3)}"
    test_pwd = secrets.token_urlsafe(24)
    test_uid = None
    # crear usuario de prueba + login real
    import chat_db as cdb

    async def setup():
        db = cdb.ChatDB(DSN); await db.init()
        created = await db.create_user(test_user, "FABLE CDP Test", test_pwd, role="user")
        await db.close()
        return created["id"]

    async def cleanup(uid):
        import asyncpg
        c = await asyncpg.connect(DSN)
        if uid:
            await c.execute("DELETE FROM soul_v3.chat_sessions WHERE user_id=$1", uid)
            await c.execute("DELETE FROM soul_v3.chat_users WHERE id=$1", uid)
        await c.close()

    test_uid = asyncio.run(setup())
    data = json.dumps({"username": test_user, "password": test_pwd}).encode()
    req = urllib.request.Request(f"{SERVER}/api/auth/login", data=data,
                                 headers={"Content-Type": "application/json"})
    token = json.loads(urllib.request.urlopen(req).read())["token"]

    b = CDP(headless=True, user_data_dir=_fresh_profile("/tmp/seal_cdp_test_auth"))
    try:
        b._send("Network.enable"); b._send("Network.clearBrowserCookies")
        b.navigate(PROTECTED)
        s1 = [r for r in b.network_requests(with_headers=True)
              if "/api/auth/me" in (r.get("url") or "")][0]
        check("Fase 1: captura status de red", s1["status"] == 401, f"status={s1['status']}")
        check("Fase 2: sent_cookie False sin sesión", s1["sent_cookie"] is False)
        ok = b.set_cookie("seal_token", token, url=SERVER)
        check("Fase 3: set_cookie aceptada", ok is True)
        b.navigate(PROTECTED)
        s2 = [r for r in b.network_requests(with_headers=True)
              if "/api/auth/me" in (r.get("url") or "")][0]
        check("Fase 3: cookie viaja (sent_cookie True)", s2["sent_cookie"] is True,
              f"cookies={s2.get('sent_cookies')}")
        check("E2E: 401 sin sesión -> 200 con sesión", s2["status"] == 200,
              f"status={s2['status']}")
        check("E2E: contenido correcto (application/json)", "json" in (s2["mime"] or ""),
              f"mime={s2['mime']}")
        # Descarga autenticada por efecto (las MANOS): save_url baja el recurso protegido
        dl_path = "/tmp/seal_cdp_test_saveurl.json"
        if os.path.exists(dl_path):
            os.remove(dl_path)
        r = b.save_url(PROTECTED, dl_path)
        got = os.path.getsize(dl_path) if os.path.exists(dl_path) else 0
        check("Descarga: save_url baja recurso autenticado (200 + bytes)",
              r.get("status") == 200 and got > 0, f"status={r.get('status')} bytes={got}")
        os.path.exists(dl_path) and os.remove(dl_path)
    finally:
        b.close()
        asyncio.run(cleanup(test_uid))
        print("  (usuario de prueba borrado)")
    import asyncpg
    async def residue():
        c = await asyncpg.connect(DSN)
        try:
            return await c.fetchval(
                "SELECT count(*) FROM soul_v3.chat_users WHERE username=$1", test_user)
        finally:
            await c.close()
    check("cleanup usuario/sesion temporal", asyncio.run(residue()) == 0)


def phase_6_advanced():
    """Fase 6: waits robustos · emulación · multi-pestaña · accessibility · performance."""
    print("\n[Fase 6] waits + emulación + multi-tab + a11y + performance")
    b = CDP(headless=True, user_data_dir=_fresh_profile("/tmp/seal_cdp_test_adv"))
    try:
        # página con contenido semántico + un elemento que aparece tarde
        page = ("data:text/html,<h1>Panel</h1><button aria-label='Entrar'>Go</button>"
                "<script>setTimeout(()=>{const d=document.createElement('div');"
                "d.id='late';d.textContent='listo';document.body.appendChild(d);},600)</script>")
        b.navigate(page, settle=0.3)
        check("wait_for elemento que aparece tarde", b.wait_for("#late", timeout=5) is True)
        check("wait_for_text encuentra texto", b.wait_for_text("listo", timeout=3) is True)
        check("wait_for ausente da False (timeout corto)", b.wait_for("#nope", timeout=1) is False)
        # emulación de dispositivo (mobile=False = viewport determinista; con mobile=True
        # sin <meta viewport> Chrome usa su ancho de layout default 980 — no es bug).
        b.emulate_device(390, 844, mobile=False)
        check("emulate_device aplica viewport", b.eval_js("window.innerWidth") == 390,
              f"innerWidth={b.eval_js('window.innerWidth')}")
        b.clear_emulation()
        # a11y snapshot
        ax = b.accessibility_tree()
        names = [n.get("name") for n in ax]
        check("accessibility_tree trae nodos con nombre", any("Entrar" in (n or "") for n in names),
              f"{len(ax)} nodos")
        snapshot = b.accessibility_snapshot()
        def walk(items):
            for item in items:
                yield item
                yield from walk(item.get("children", []))
        ax_nodes = list(walk(snapshot["roots"]))
        check("accessibility_snapshot preserva jerarquia AX",
              snapshot["node_count"] == len(ax_nodes)
              and snapshot["truncated"] is False
              and any(node.get("role") == "button" and node.get("name") == "Entrar"
                      for node in ax_nodes)
              and any(node.get("children") for node in ax_nodes),
              f"{snapshot['node_count']}/{snapshot['total_nodes']} nodos")
        # performance metrics
        pm = b.performance_metrics()
        check("performance_metrics devuelve métricas", isinstance(pm, dict) and len(pm) > 3,
              f"{len(pm)} métricas")
        # multi-pestaña
        before = len(b.tabs())
        tid = b.open_tab("data:text/html,<h1>tab2</h1>")
        b.wait_for_text("tab2", timeout=3)
        check("open_tab crea y enfoca pestaña nueva", "tab2" in (b.text() or ""),
              f"tabs: {before}->{len(b.tabs())}")
    finally:
        b.close()


def phase_7_load_trace_policy():
    """Carga por eventos + network-idle + trace completo + allowlist previa a red."""
    print("\n[Fase 7] load events + network-idle + trace + origin gate/audit")
    first, second = _server(), _server()
    origin = f"http://127.0.0.1:{first.server_port}"
    foreign = f"http://127.0.0.1:{second.server_port}/foreign"
    profile = _fresh_profile("/tmp/seal_cdp_test_trace")
    trace_path = "/tmp/seal_cdp_test_trace.json"
    audit_path = "/tmp/seal_cdp_test_audit.jsonl"
    audit_secret = "SEAL_AUDIT_SECRET_7f91"
    for path in (trace_path, audit_path):
        try: os.remove(path)
        except FileNotFoundError: pass
    before_foreign = _DeterministicHandler.foreign_hits
    b = CDP(headless=True, user_data_dir=profile, allowed_origins=[origin],
            allowed_methods=["GET", "HEAD"],
            audit_path=audit_path, purpose="real CDP regression suite")
    try:
        check("read-only hardening activo", b.harden_read_only() is True)
        b.start_trace()
        b.navigate(origin + "/", wait_until="networkidle", timeout=8, network_idle=0.3)
        check("navigate(networkidle) espera fetch tardio", b.query("#done") == "done")
        trace = b.stop_trace(trace_path)
        payload = json.load(open(trace, encoding="utf-8"))
        check("performance trace JSON completo",
              len(payload.get("traceEvents", [])) > 10,
              f"{len(payload.get('traceEvents', []))} eventos")
        # Intento cross-origin: el gate lo debe cortar antes de tocar el servidor B.
        b.eval_js(f"fetch({foreign!r}).catch(()=>null)")
        b._drain_events(0.5)
        denied = [event for event in b.policy_events()
                  if not event.get("allowed") and event.get("url") == foreign]
        check("origin gate bloquea antes de red", bool(denied))
        check("origen denegado tuvo cero efecto remoto",
              _DeterministicHandler.foreign_hits == before_foreign,
              f"hits={_DeterministicHandler.foreign_hits-before_foreign}")

        # Una navegación principal al origen prohibido también debe fallar antes de red.
        before_main_nav = _DeterministicHandler.foreign_hits
        try:
            b.navigate(foreign, wait_until="none")
            main_nav_blocked = False
        except RuntimeError as exc:
            main_nav_blocked = "ERR_BLOCKED_BY_CLIENT" in str(exc)
        check("origin gate bloquea navegacion principal", main_nav_blocked)
        check("navegacion principal denegada tuvo cero efecto remoto",
              _DeterministicHandler.foreign_hits == before_main_nav,
              f"hits={_DeterministicHandler.foreign_hits-before_main_nav}")

        # Aunque el origen sea valido, un metodo con efecto queda bloqueado antes de red.
        before_post = _DeterministicHandler.post_hits
        b.navigate(origin + "/", wait_until="domcontentloaded")
        b.eval_js("fetch('/', {method:'POST', body:'must-not-arrive'}).catch(()=>null)")
        b._drain_events(0.5)
        denied_post = [event for event in b.policy_events()
                       if event.get("method") == "POST" and not event.get("allowed")]
        check("method gate bloquea POST en origen permitido", bool(denied_post))
        check("POST denegado tuvo cero efecto remoto",
              _DeterministicHandler.post_hits == before_post,
              f"hits={_DeterministicHandler.post_hits-before_post}")

        # El audit puede conservar ruta/origen, nunca query credentials ni cuerpos data:.
        b.navigate(origin + f"/?token={audit_secret}&authorization=Bearer-{audit_secret}")
        b.navigate(f"data:text/html,<h1>{audit_secret}</h1>")
        audit = [json.loads(line) for line in open(audit_path, encoding="utf-8") if line.strip()]
        audit_raw = "\n".join(json.dumps(row, sort_keys=True) for row in audit)
        check("audit JSONL registra allow/deny sin secretos",
              any(row.get("allowed") is False for row in audit)
              and audit_secret not in audit_raw
              and "token=" not in audit_raw.lower()
              and "authorization=" not in audit_raw.lower()
              and all("cookie" not in json.dumps(row).lower() for row in audit),
              f"{len(audit)} eventos")
    finally:
        b.close()
        first.shutdown(); second.shutdown()
        first.server_close(); second.server_close()


def phase_8_isolation_and_idempotence():
    """Dos instancias simultáneas no comparten puerto/profile/cookies; close es seguro 2x."""
    print("\n[Fase 8] aislamiento concurrente + close idempotente")
    a = CDP(headless=True, user_data_dir=_fresh_profile("/tmp/seal_cdp_test_conc_a"))
    b = CDP(headless=True, user_data_dir=_fresh_profile("/tmp/seal_cdp_test_conc_b"))
    try:
        check("puertos dinamicos distintos", a.port != b.port, f"{a.port} != {b.port}")
        a.set_cookie("isolation_probe", "only-a", url="http://localhost:8765")
        names_b = {cookie.get("name") for cookie in b.cookies()}
        check("profiles no comparten cookies", "isolation_probe" not in names_b)
    finally:
        a.close(); a.close()
        b.close(); b.close()
    check("close() idempotente", a._closed and b._closed)

    # Inputs inválidos deben fallar antes de crear un profile propio.
    profiles_before = {entry.name for entry in os.scandir("/tmp")
                       if entry.name.startswith("seal_cdp_") and entry.is_dir()}
    try:
        CDP(allowed_origins=[])
        rejected_empty_policy = False
    except ValueError:
        rejected_empty_policy = True
    try:
        CDP(browser="seal-browser-that-does-not-exist")
        rejected_missing_browser = False
    except FileNotFoundError:
        rejected_missing_browser = True
    try:
        CDP(allowed_methods=[])
        rejected_empty_methods = False
    except ValueError:
        rejected_empty_methods = True
    try:
        CDP(allowed_methods=["GET"])
        rejected_methods_without_origins = False
    except ValueError:
        rejected_methods_without_origins = True
    profiles_after = {entry.name for entry in os.scandir("/tmp")
                      if entry.name.startswith("seal_cdp_") and entry.is_dir()}
    check("constructor invalido falla cerrado",
          rejected_empty_policy and rejected_missing_browser and rejected_empty_methods
          and rejected_methods_without_origins)
    check("constructor invalido no filtra profiles temporales",
          profiles_after == profiles_before,
          f"nuevos={sorted(profiles_after-profiles_before)}")

    owned = CDP(headless=True)
    owned_profile = owned._own_profile
    owned.close()
    time.sleep(0.2)
    check("close() elimina el profile temporal propio",
          bool(owned_profile) and not os.path.exists(owned_profile),
          f"profile={owned_profile}")


def phase_9_mcp_server_lifecycle():
    """Cerrar el cliente stdio debe cerrar Brave aunque nadie llame close_browser.

    Este es el camino real de reinicio de un agente. Antes del fix, FastMCP terminaba pero el
    Brave lanzado con ``start_new_session`` sobrevivía como huérfano junto con su profile.
    """
    print("\n[Fase 9] ciclo de vida MCP/stdio + teardown implicito")
    base = "/tmp/seal_cdp_mcp_shutdown_test"
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)

    async def run_client():
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = os.environ.copy()
        env["TMPDIR"] = base
        params = StdioServerParameters(
            command="python3",
            args=[os.path.join(os.path.dirname(__file__), "seal_cdp_mcp.py")],
            env=env,
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    "browse",
                    {"url": SERVER, "wait_until": "domcontentloaded"},
                )
                return not bool(result.isError)
        # Salir de ambos contextos cierra stdio/servidor SIN close_browser explícito.

    browse_ok = asyncio.run(run_client())
    time.sleep(0.5)
    marker = base.encode()
    residual_pids = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            raw = open(f"/proc/{entry.name}/cmdline", "rb").read()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if marker in raw and (b"brave" in raw.lower() or b"chromium" in raw.lower()):
            residual_pids.append(int(entry.name))
    # Brave también crea scratch dirs ``org.chromium.*`` bajo TMPDIR; no son el profile
    # persistente de CDP. El contrato exacto es que desaparezca ``seal_cdp_*``.
    profiles = [entry.path for entry in os.scandir(base)
                if entry.is_dir() and entry.name.startswith("seal_cdp_")]
    check("MCP browse real antes del cierre", browse_ok)
    check("salida MCP deja 0 procesos Brave", not residual_pids, f"pids={residual_pids}")
    check("salida MCP elimina profile propio", not profiles, f"profiles={profiles}")
    shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    try:
        phase_1_2_dom_input()
        phase_3_network_auth()
        phase_6_advanced()
        phase_7_load_trace_policy()
        phase_8_isolation_and_idempotence()
        phase_9_mcp_server_lifecycle()
    finally:
        # El contrato no es solo "sin procesos": tampoco deja profiles de test.
        for suffix in ("dom", "auth", "adv", "trace", "conc_a", "conc_b"):
            shutil.rmtree(f"/tmp/seal_cdp_test_{suffix}", ignore_errors=True)
        for path in ("/tmp/seal_cdp_test_shot.png", "/tmp/seal_cdp_test_trace.json",
                     "/tmp/seal_cdp_test_audit.jsonl"):
            try: os.remove(path)
            except FileNotFoundError: pass
    zombies = _profile_processes()
    check("close() deja 0 procesos CDP de la suite", not zombies, f"pids={zombies}")
    # Un renderer puede terminar milisegundos después del proceso padre y recrear un
    # directorio caller-owned vacío tras el primer rmtree. Con cero procesos confirmado,
    # repetimos la limpieza exacta de las seis rutas conocidas y la hacemos parte del gate.
    for suffix in ("dom", "auth", "adv", "trace", "conc_a", "conc_b"):
        shutil.rmtree(f"/tmp/seal_cdp_test_{suffix}", ignore_errors=True)
    profile_leftovers = [f"/tmp/seal_cdp_test_{suffix}"
                         for suffix in ("dom", "auth", "adv", "trace", "conc_a", "conc_b")
                         if os.path.exists(f"/tmp/seal_cdp_test_{suffix}")]
    check("suite deja 0 profiles temporales", not profile_leftovers,
          f"paths={profile_leftovers}")
    leftovers = [path for path in ("/tmp/seal_cdp_test_shot.png",
                                   "/tmp/seal_cdp_test_trace.json",
                                   "/tmp/seal_cdp_test_audit.jsonl")
                 if os.path.exists(path)]
    check("suite deja 0 artefactos temporales", not leftovers, f"paths={leftovers}")
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n=== RESULTADO: {passed}/{total} PASS ===")
    sys.exit(0 if passed == total else 1)
