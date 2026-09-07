#!/usr/bin/env python3
"""Native SEAL Chrome DevTools layer — EL navegador nativo del equipo.

VISIÓN (William, 18-jul): *"ese MCP será el que usen TODOS ustedes SÍ O SÍ para navegar."*
Esta es la **capacidad canónica y compartida** con la que cada agente SEAL navega la web —
sus **ojos Y MANOS**: ver, leer, clickear, escribir, DESCARGAR y actuar. No es un primitivo
de seguridad ni un fetch read-only: es una herramienta que EMPODERA, enabling por defecto.

Orden original: estudiar chrome-devtools-mcp y reescribirlo NATIVO. La referencia usa
Puppeteer (wrapper pesado de Node) sobre CDP. Aquí hablamos CDP DIRECTO desde Python — cero
Puppeteer y cero Node. Solo ``websocket-client`` para el transporte. Liviano, auditable, nuestro.

Capacidades (enabling por defecto): navegación por eventos de carga/network-idle,
red/headers/cookies, DOM, screenshot, input (click/type), descarga autenticada (`save_url`),
waits, emulación, pestañas, accesibilidad, métricas, traces completos.

GOBERNANZA = OPT-IN, NO el default. `allowed_origins`, `harden_read_only()` y el audit JSONL
existen para contextos sensibles que NEXUS decida gatear — hay que invocarlos a propósito.
Sin ellos, el tool está en su modo natural: enabling. No confundir "tiene guardarraíles
opcionales" con "es read-only".

Uso (una línea, para cualquier agente):
    with CDP() as b:                       # lanza Brave headless; puerto/profile propios
        b.navigate("http://localhost:8765/", wait_until="networkidle")
        print(b.text())                    # ver la página
        b.click("#btn"); b.type_text("#q", "hola")   # actuar
        b.save_url("http://localhost:8765/uploads/x.pdf", "/tmp/x.pdf")  # descargar
"""
import json
import ipaddress
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import re
import websocket  # websocket-client (síncrono, simple)

from mcp_web_soul_proxy import EgressPolicy, PinnedEgressProxy


def _free_port():
    """Un puerto TCP libre asignado por el SO. NECESARIO: el puerto fijo 9222 hacía que
    instancias concurrentes (o zombies de un close() incompleto) colisionaran — un CDP
    nuevo se adjuntaba al navegador VIEJO (con su cookie/sesión) en vez de uno limpio.
    Cazado por efecto: navegar a un recurso protegido daba 200 SIN cookie (era otra sesión)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class CDP:
    def __init__(self, port=None, browser="brave-browser", headless=True, url="about:blank",
                 launch=True, user_data_dir=None, allowed_origins=None, audit_path=None,
                 purpose="interactive browser verification", allowed_methods=None,
                 deny_private_networks=False, process_env=None):
        # Puerto dinámico por instancia (default): sin esto, instancias colisionan en 9222.
        if not launch and port is None:
            raise ValueError("CDP: launch=False requiere port explicito")
        self.port = port or _free_port()
        self._msg_id = 0
        self._proc = None
        self.ws = None
        self._events = []
        self._responses = {}
        self._closed = False
        self._egress_proxy = None
        self._proxy_username = ""
        self._proxy_password = ""
        self._tracing = False
        self._audit_path = audit_path
        self._purpose = str(purpose or "interactive browser verification")[:300]
        # Validar toda entrada que puede fallar ANTES de crear el profile temporal. Antes,
        # ``allowed_origins=[]`` y un browser inexistente dejaban ``/tmp/seal_cdp_*``
        # huérfano aunque el constructor nunca llegara a lanzar Brave.
        self._allowed_origins = None
        self._deny_private_networks = bool(deny_private_networks)
        if allowed_origins is not None:
            self._allowed_origins = frozenset(
                self._canonical_origin(origin) for origin in allowed_origins
            )
            if not self._allowed_origins:
                raise ValueError("CDP: allowed_origins no puede estar vacio")
        self._allowed_methods = None
        if allowed_methods is not None:
            self._allowed_methods = frozenset(
                str(method).strip().upper() for method in allowed_methods
                if str(method).strip()
            )
            if not self._allowed_methods:
                raise ValueError("CDP: allowed_methods no puede estar vacio")
            if self._allowed_origins is None and not self._deny_private_networks:
                raise ValueError(
                    "CDP: allowed_methods requiere allowed_origins o deny_private_networks"
                )
        resolved_browser = None
        if launch:
            resolved_browser = shutil.which(browser) if os.sep not in browser else browser
            if not resolved_browser or not os.path.exists(resolved_browser):
                raise FileNotFoundError(f"CDP: navegador no encontrado: {browser}")

        # Profile único por instancia salvo que el caller fije uno: evita que dos instancias
        # concurrentes compartan cookies/estado. Se borra en close() si lo creamos nosotros.
        self._own_profile = None
        if launch and user_data_dir is None:
            user_data_dir = tempfile.mkdtemp(prefix="seal_cdp_")
            self._own_profile = user_data_dir
        if launch:
            if self._allowed_origins is not None or self._deny_private_networks:
                self._egress_proxy = PinnedEgressProxy(EgressPolicy(
                    allowed_origins=self._allowed_origins,
                    allowed_methods=self._allowed_methods,
                    deny_private_networks=self._deny_private_networks,
                )).start()
                self._proxy_username = self._egress_proxy.username
                self._proxy_password = self._egress_proxy.password
            args = [resolved_browser, f"--remote-debugging-port={self.port}",
                    f"--user-data-dir={user_data_dir}", "--no-first-run",
                    "--no-default-browser-check", url]
            if self._egress_proxy is not None:
                args[1:1] = [
                    f"--proxy-server=http://127.0.0.1:{self._egress_proxy.port}",
                    "--proxy-bypass-list=<-loopback>",
                    "--disable-quic",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                ]
            if headless:
                args.insert(1, "--headless=new")
            # start_new_session=True: el navegador arranca en su propio grupo de procesos,
            # así close() puede matar TODO el árbol (renderers incluidos) con killpg. Sin
            # esto, terminate() dejaba renderers zombie acumulándose en cada corrida.
            try:
                self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL, start_new_session=True,
                                              env=process_env)
                self._wait_ready()
            except Exception:
                self.close()
                raise
        try:
            self.ws = self._attach_page()
            if self._allowed_origins is not None or self._deny_private_networks:
                self._send("Fetch.enable", {
                    "patterns": [{"urlPattern": "*", "requestStage": "Request"}],
                    "handleAuthRequests": True,
                })
        except Exception:
            self.close()
            raise

    @staticmethod
    def _canonical_origin(url):
        """Origen exacto scheme://host:port para la compuerta previa a red."""
        parsed = urllib.parse.urlsplit(str(url))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"CDP: origen invalido: {url!r}")
        if parsed.username or parsed.password:
            raise ValueError("CDP: userinfo prohibido en origen")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        return f"{parsed.scheme}://{host}:{port}"

    @staticmethod
    def _host_is_public(host):
        """Allow only hosts whose complete DNS answer set is globally routable."""
        normalized = str(host or "").rstrip(".").lower()
        if not normalized or normalized == "localhost" or normalized.endswith(".local"):
            return False
        try:
            literal = ipaddress.ip_address(normalized)
            return literal.is_global
        except ValueError:
            pass
        try:
            addresses = {
                item[4][0]
                for item in socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
            }
        except (OSError, socket.gaierror):
            return False
        if not addresses:
            return False
        try:
            return all(ipaddress.ip_address(address).is_global for address in addresses)
        except ValueError:
            return False

    @staticmethod
    def _host_is_obviously_private(host):
        """Bloquea nombres/literales locales sin resolver dos veces el DNS.

        Los dominios públicos se resuelven y se fijan exclusivamente dentro del
        proxy de egress. Resolver aquí también creaba un TOCTOU y falsos bloqueos
        cuando una de las dos consultas DNS fallaba transitoriamente.
        """
        normalized = str(host or "").rstrip(".").lower()
        if not normalized or normalized == "localhost" or normalized.endswith(".local"):
            return True
        try:
            return not ipaddress.ip_address(normalized).is_global
        except ValueError:
            return False

    @staticmethod
    def _sanitize_audit_url(value):
        """Conserva ruta/origen útiles sin persistir query, fragment, userinfo o data.

        El audit anterior filtraba headers pero escribía la URL cruda. Una URL como
        ``/?token=...`` o ``data:text/html,<secret>`` filtraba credenciales/cuerpo al JSONL.
        """
        raw = str(value or "")
        try:
            parsed = urllib.parse.urlsplit(raw)
        except ValueError:
            return "[redacted-url]"
        scheme = parsed.scheme.lower()
        if scheme in {"data", "blob"}:
            return f"{scheme}:[redacted]"
        if scheme == "about":
            return "about:blank" if parsed.path == "blank" else "about:[redacted]"
        if scheme not in {"http", "https"}:
            return f"{scheme}:[redacted]" if scheme else "[redacted-url]"
        try:
            host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
            port = parsed.port
        except (UnicodeError, ValueError):
            return f"{scheme}://[redacted]"
        if not host:
            return f"{scheme}://[redacted]"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = f"{host}:{port}" if port is not None else host
        return urllib.parse.urlunsplit((scheme, netloc, parsed.path or "/", "", ""))

    @staticmethod
    def _sanitize_audit_text(value):
        """Redacta pares credential-like también en purpose/reason de texto libre."""
        return re.sub(
            r"(?i)\b(token|authorization|cookie|password|secret)\s*[:=]\s*[^\s,;]+",
            lambda match: f"{match.group(1)}=[redacted]",
            str(value),
        )

    def _audit(self, event, **fields):
        """Audit JSONL sin credenciales/cuerpos. Best-effort: no oculta el resultado CDP."""
        if not self._audit_path:
            return
        sensitive_fragments = {
            "authorization", "cookie", "password", "secret", "token", "body",
            "payload", "post_data", "headers", "credential",
        }
        safe_fields = {}
        for key, value in fields.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in sensitive_fragments):
                continue
            if "url" in lowered:
                safe_fields[key] = self._sanitize_audit_url(value)
            elif isinstance(value, str):
                safe_fields[key] = self._sanitize_audit_text(value)
            else:
                safe_fields[key] = value
        record = {
            "ts": time.time(), "event": event,
            "purpose": self._sanitize_audit_text(self._purpose),
            **safe_fields,
        }
        path = os.path.abspath(os.path.expanduser(str(self._audit_path)))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            pass

    def _wait_ready(self, timeout=15):
        """El endpoint HTTP /json/version aparece cuando el navegador está listo."""
        for _ in range(timeout * 2):
            try:
                urllib.request.urlopen(f"http://localhost:{self.port}/json/version", timeout=1)
                return
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("CDP: el navegador no expuso el puerto de debugging a tiempo")

    def _attach_page(self):
        """Toma el primer target de tipo 'page' y abre el websocket CDP a él."""
        targets = json.load(urllib.request.urlopen(f"http://localhost:{self.port}/json"))
        page = next((t for t in targets if t.get("type") == "page"), None)
        if not page:
            raise RuntimeError("CDP: no hay target 'page'")
        # suppress_origin=True OBLIGATORIO: Chrome/Brave rechazan el handshake WS si llega
        # un header Origin (protección anti-DNS-rebinding). websocket-client lo manda por
        # defecto → 403 en el handshake. Cazado por efecto (Chrome 150). Sin este flag, la
        # capa entera no conecta.
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], suppress_origin=True)
        return ws

    def _handle_event(self, msg):
        """Procesa eventos que requieren respuesta y conserva todos para inspección."""
        method = msg.get("method")
        params = msg.get("params", {})
        self._events.append(msg)
        if method == "Fetch.authRequired" and self._egress_proxy is not None:
            challenge = params.get("authChallenge", {}) or {}
            source = str(challenge.get("source") or "").lower()
            response = {
                "response": "ProvideCredentials" if source == "proxy" else "CancelAuth",
            }
            if source == "proxy":
                response.update({
                    "username": self._proxy_username,
                    "password": self._proxy_password,
                })
            self._send("Fetch.continueWithAuth", {
                "requestId": params.get("requestId"),
                "authChallengeResponse": response,
            })
            self._audit("proxy_auth", ok=source == "proxy", source=source)
            return
        if method != "Fetch.requestPaused" or (
            self._allowed_origins is None and not self._deny_private_networks
        ):
            return
        request = params.get("request", {}) or {}
        url = request.get("url", "")
        request_method = str(request.get("method") or "GET").upper()
        allowed = False
        reason = "unsupported scheme"
        parsed = urllib.parse.urlsplit(url)
        # about:/data:/blob: no salen a red. Se permiten para fixtures y contenido
        # ya originado dentro de la pestaña; toda red http(s) sí cruza la allowlist.
        if parsed.scheme in {"about", "data", "blob"}:
            allowed, reason = True, "non-network scheme"
        elif parsed.scheme in {"http", "https"}:
            try:
                origin = self._canonical_origin(url)
                if self._deny_private_networks and self._host_is_obviously_private(parsed.hostname):
                    allowed = False
                    reason = "private destination denied"
                elif self._allowed_origins is not None:
                    allowed = origin in self._allowed_origins
                    reason = "origin allowed" if allowed else f"origin denied: {origin}"
                else:
                    # La resolución y el chequeo de TODAS las IPs sucede una sola
                    # vez en PinnedEgressProxy, justo antes de connect(sockaddr).
                    allowed, reason = True, "destination delegated to pinned proxy"
            except ValueError as exc:
                reason = str(exc)
        if allowed and self._allowed_methods is not None and request_method not in self._allowed_methods:
            allowed = False
            reason = f"method denied: {request_method}"
        request_id = params.get("requestId")
        if allowed:
            self._send("Fetch.continueRequest", {"requestId": request_id})
        else:
            self._send("Fetch.failRequest", {
                "requestId": request_id, "errorReason": "BlockedByClient"
            })
        gate_event = {
            "method": "SEAL.requestGate",
            "params": {
                "url": url,
                "method": request_method,
                "allowed": allowed,
                "reason": reason,
            },
        }
        self._events.append(gate_event)
        self._audit(
            "request_gate", url=url, method=request_method,
            allowed=allowed, reason=reason,
        )

    def _receive_one(self, timeout=None):
        previous = self.ws.gettimeout()
        self.ws.settimeout(timeout)
        try:
            msg = json.loads(self.ws.recv())
        finally:
            self.ws.settimeout(previous)
        if "id" in msg:
            self._responses[msg["id"]] = msg
        else:
            self._handle_event(msg)
        return msg

    def _send(self, method, params=None):
        """Comando CDP síncrono, reentrante y con buffer de respuestas/eventos.

        La reentrancia es necesaria para contestar Fetch.requestPaused mientras un
        Page.navigate sigue pendiente. Sin buffer por id, el handler podía consumir la
        respuesta del comando exterior y bloquear la navegación indefinidamente.
        """
        self._msg_id += 1
        mid = self._msg_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = self._responses.pop(mid, None)
            if msg is None:
                self._receive_one(timeout=None)
                continue
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} error: {msg['error']}")
                return msg.get("result", {})

    def _drain_events(self, duration=0.2):
        """Drena eventos durante una ventana acotada sin dejar timeout en el socket."""
        deadline = time.monotonic() + max(0.0, float(duration))
        while time.monotonic() < deadline:
            try:
                self._receive_one(timeout=min(0.1, deadline - time.monotonic()))
            except (websocket.WebSocketTimeoutException, TimeoutError):
                pass

    def _wait_for_event(self, method, timeout=15.0):
        """Espera un evento CDP; reconoce los ya bufferizados y CADA evento nuevo exactamente
        una vez (cursor).

        FIX de flake (cazado por efecto en `domcontentloaded`): antes chequeaba solo
        ``self._events[-1]`` tras cada ``_receive_one``. Pero ``_receive_one`` puede traer una
        RESPUESTA de comando (va a ``_responses``, NO se anexa a ``_events``) o un evento
        distinto → ``[-1]`` quedaba stale y el evento objetivo se perdía → timeout intermitente.
        El cursor revisa todos los eventos sin depender de la última posición."""
        idx = 0
        deadline = time.monotonic() + timeout
        while True:
            while idx < len(self._events):
                if self._events[idx].get("method") == method:
                    return self._events[idx]
                idx += 1
            if time.monotonic() >= deadline:
                raise TimeoutError(f"CDP: timeout esperando {method}")
            try:
                self._receive_one(timeout=min(0.25, deadline - time.monotonic()))
            except (websocket.WebSocketTimeoutException, TimeoutError):
                continue

    def navigate(self, url, settle=0.0, wait_until="load", timeout=15.0,
                 network_idle=0.5):
        """Navega y espera una señal real de carga en vez de un sleep fijo.

        wait_until: ``load`` (default), ``domcontentloaded``, ``networkidle`` o
        ``none``. ``settle`` queda como gracia adicional compatible con callers viejos,
        pero ya no es el mecanismo primario. Devuelve la respuesta de Page.navigate.
        """
        wait_until = str(wait_until).lower()
        if wait_until not in {"load", "domcontentloaded", "networkidle", "none"}:
            raise ValueError(f"CDP navigate: wait_until invalido: {wait_until}")
        self._send("Network.enable")
        self._send("Page.enable")
        self._events.clear()
        result = self._send("Page.navigate", {"url": url})
        if result.get("errorText"):
            self._audit("navigate", url=url, ok=False, error=result["errorText"])
            raise RuntimeError(f"CDP navigate: {result['errorText']} ({url})")
        if wait_until == "load":
            self._wait_for_event("Page.loadEventFired", timeout=timeout)
        elif wait_until == "domcontentloaded":
            self._wait_for_event("Page.domContentEventFired", timeout=timeout)
        elif wait_until == "networkidle":
            self._wait_for_event("Page.domContentEventFired", timeout=timeout)
            self.wait_for_network_idle(idle_seconds=network_idle, timeout=timeout)
        if settle:
            self._drain_events(settle)
        else:
            self._drain_events(0.1)
        self._audit("navigate", url=url, ok=True, wait_until=wait_until,
                    final_url=self.eval_js("location.href"))
        return result

    def wait_for_network_idle(self, idle_seconds=0.5, timeout=15.0, max_inflight=0):
        """Espera que las requests en vuelo permanezcan <= max_inflight.

        Cuenta requestWillBeSent y descuenta loadingFinished/loadingFailed usando el
        requestId real. Es más fuerte que dormir N segundos y tiene timeout explícito.
        """
        inflight = set()

        def apply(event):
            method = event.get("method")
            request_id = (event.get("params") or {}).get("requestId")
            if not request_id:
                return
            if method == "Network.requestWillBeSent":
                inflight.add(request_id)
            elif method in {"Network.loadingFinished", "Network.loadingFailed"}:
                inflight.discard(request_id)

        for event in self._events:
            apply(event)
        idle_since = time.monotonic() if len(inflight) <= max_inflight else None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if idle_since is not None and time.monotonic() - idle_since >= idle_seconds:
                return True
            try:
                before = len(self._events)
                self._receive_one(timeout=min(0.1, deadline - time.monotonic()))
                for event in self._events[before:]:
                    apply(event)
            except (websocket.WebSocketTimeoutException, TimeoutError):
                pass
            if len(inflight) <= max_inflight:
                idle_since = idle_since or time.monotonic()
            else:
                idle_since = None
        raise TimeoutError(
            f"CDP: network idle timeout ({len(inflight)} requests en vuelo)"
        )

    def policy_events(self):
        """Decisiones de la compuerta de origen, sin headers/cookies/cuerpos."""
        return [event.get("params", {}) for event in self._events
                if event.get("method") == "SEAL.requestGate"]

    def harden_read_only(self):
        """Endurece la instancia para lectura web gobernada.

        Evita que un service worker intercepte requests fuera de la compuerta visible y
        deniega descargas del navegador. La allowlist de origen/metodo sigue siendo la
        autoridad previa a cada efecto remoto.
        """
        self._send("Network.enable")
        self._send("Network.setBypassServiceWorker", {"bypass": True})
        try:
            self._send("Browser.setDownloadBehavior", {"behavior": "deny"})
        except RuntimeError:
            # Compatibilidad con builds Chromium que aun exponen el dominio legado.
            self._send("Page.setDownloadBehavior", {"behavior": "deny"})
        self._audit(
            "read_only_hardening", ok=True,
            allowed_methods=sorted(self._allowed_methods or []),
        )
        return True

    def network_requests(self, with_headers=False):
        """Respuestas de red capturadas: url, status, mime, remoteIP. Con with_headers=True
        agrega req_headers y resp_headers (Fase 2) — sirve para detectar CORS
        (Access-Control-*) y si el request mandó la cookie de sesión.

        GOTCHA nativo de CDP (cazado por efecto, Fase 3): las cookies y varios headers de
        conexión NO viajan en `Network.requestWillBeSent.request.headers` — Chrome los agrega
        después, en el stack de red, y los reporta en `Network.requestWillBeSentExtraInfo`
        (`headers` + `associatedCookies`). Igual, los headers reales de respuesta (incl.
        Set-Cookie y varios CORS) están en `responseReceivedExtraInfo`, no solo en
        `responseReceived`. Fusionamos ambos ExtraInfo o `sent_cookie`/CORS dan FALSO NEGATIVO."""
        req_hdrs, req_extra, assoc_cookies, resp_extra = {}, {}, {}, {}
        for e in self._events:
            m = e.get("method")
            p = e.get("params", {})
            rid = p.get("requestId")
            if m == "Network.requestWillBeSent":
                req_hdrs[rid] = (p.get("request", {}) or {}).get("headers", {})
            elif m == "Network.requestWillBeSentExtraInfo":
                req_extra[rid] = p.get("headers", {}) or {}
                # cookies efectivamente enviadas (blockedReasons vacío = viajó)
                assoc_cookies[rid] = [c for c in (p.get("associatedCookies") or [])
                                      if not c.get("blockedReasons")]
            elif m == "Network.responseReceivedExtraInfo":
                resp_extra[rid] = p.get("headers", {}) or {}
        out = {}
        for e in self._events:
            p = e.get("params", {})
            if e.get("method") == "Network.responseReceived":
                r = p.get("response", {})
                rid = p.get("requestId")
                rec = {"url": r.get("url"), "status": r.get("status"),
                       "mime": r.get("mimeType"), "remoteIP": r.get("remoteIPAddress")}
                if with_headers:
                    # req = base (requestWillBeSent) + ExtraInfo (trae la cookie real)
                    merged_req = {**req_hdrs.get(rid, {}), **req_extra.get(rid, {})}
                    merged_resp = {**(r.get("headers", {}) or {}), **resp_extra.get(rid, {})}
                    rec["req_headers"] = merged_req
                    rec["resp_headers"] = merged_resp
                    rh = {k.lower(): v for k, v in merged_req.items()}
                    sh = {k.lower(): v for k, v in merged_resp.items()}
                    # sent_cookie: header Cookie presente O cookies asociadas no bloqueadas
                    rec["sent_cookie"] = ("cookie" in rh) or bool(assoc_cookies.get(rid))
                    rec["sent_cookies"] = [c.get("cookie", {}).get("name")
                                           for c in assoc_cookies.get(rid, [])]
                    rec["cors_allow_origin"] = sh.get("access-control-allow-origin")
                out[rid] = rec
        return list(out.values())

    def cookies(self):
        return self._send("Network.getCookies").get("cookies", [])

    def set_cookie(self, name, value, url=None, domain=None, path="/",
                   http_only=False, secure=False, same_site=None):
        """Fase 3: inyecta una cookie vía CDP (Network.setCookie). Permite reproducir
        flujos AUTENTICADOS por efecto — p. ej. inyectar la cookie de sesión y luego
        pedir /uploads/x.png para ver si el render da 200 (auth OK) o 401 (cookie no
        viaja). Pasar url O domain. Devuelve True si el navegador aceptó la cookie."""
        self._send("Network.enable")
        params = {"name": name, "value": value, "path": path,
                  "httpOnly": http_only, "secure": secure}
        if url:
            params["url"] = url
        if domain:
            params["domain"] = domain
        if same_site:
            params["sameSite"] = same_site  # "Strict" | "Lax" | "None"
        return bool(self._send("Network.setCookie", params).get("success"))

    def console(self):
        return [e.get("params", {}) for e in self._events
                if e.get("method") in ("Runtime.consoleAPICalled", "Log.entryAdded")]

    # ── Fase 4: DOM + screenshot (ver la página, no solo la red) ──────────────
    def eval_js(self, expression):
        """Evalúa JS en el contexto de la página y devuelve el valor primitivo/JSON.
        Base de las demás capacidades DOM. Lanza si el JS tira excepción."""
        r = self._send("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True})
        exc = r.get("exceptionDetails")
        if exc:
            raise RuntimeError(f"CDP eval_js: {exc.get('text')} :: "
                               f"{(exc.get('exception') or {}).get('description', '')}")
        return (r.get("result") or {}).get("value")

    def html(self):
        """HTML renderizado actual (outerHTML tras ejecutar JS) — no el fuente crudo."""
        return self.eval_js("document.documentElement.outerHTML")

    def text(self):
        """Texto visible de la página (innerText) — útil para leer/verificar contenido."""
        return self.eval_js("document.body ? document.body.innerText : ''")

    def query(self, selector):
        """¿Existe el selector? Devuelve textContent recortado o None. Sonda de estado DOM."""
        js = (f"(() => {{ const e = document.querySelector({selector!r}); "
              f"return e ? (e.innerText||e.textContent||'').slice(0,500) : null; }})()")
        return self.eval_js(js)

    def screenshot(self, path, full_page=False):
        """Captura PNG de la página a `path` vía Page.captureScreenshot. full_page=True
        captura toda la altura del documento, no solo el viewport. Devuelve `path`."""
        import base64
        self._send("Page.enable")
        params = {"format": "png"}
        if full_page:
            params["captureBeyondViewport"] = True
        r = self._send("Page.captureScreenshot", params)
        data = r.get("data")
        if not data:
            raise RuntimeError("CDP screenshot: el navegador no devolvió imagen")
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path

    def save_url(self, url, path, credentials="include"):
        """Descarga el contenido de `url` a `path` — las MANOS del agente para bajar archivos.

        Usa un ``fetch`` DENTRO de la página, así viaja la sesión (cookies) del navegador:
        descarga recursos AUTENTICADOS por efecto (p. ej. un PDF protegido tras inyectar
        ``set_cookie``). Devuelve dict {status, bytes, path}. Lanza si el status no es 2xx.

        Enabling por diseño: NO depende de Browser.setDownloadBehavior (que ``harden_read_only``
        puede negar); baja los bytes vía fetch y los escribe. Para same-origin, navegá primero
        al origen o tené la cookie seteada para ese dominio."""
        import base64
        js = (
            "(async () => {"
            f"  const r = await fetch({url!r}, {{credentials: {credentials!r}}});"
            "  const buf = new Uint8Array(await r.arrayBuffer());"
            "  let bin = ''; const CH = 0x8000;"
            "  for (let i = 0; i < buf.length; i += CH)"
            "    bin += String.fromCharCode.apply(null, buf.subarray(i, i + CH));"
            "  return {status: r.status, b64: btoa(bin), type: r.headers.get('content-type')};"
            "})()"
        )
        res = self.eval_js(js) or {}
        status = res.get("status")
        if not (isinstance(status, int) and 200 <= status < 300):
            self._audit("save_url", url=url, ok=False, status=status)
            raise RuntimeError(f"CDP save_url: status {status} para {url}")
        raw = base64.b64decode(res.get("b64") or "")
        with open(path, "wb") as f:
            f.write(raw)
        self._audit("save_url", url=url, ok=True, status=status, bytes=len(raw))
        return {"status": status, "bytes": len(raw), "path": path,
                "content_type": res.get("type")}

    # ── Fase 5: automación de input (click / type) ────────────────────────────
    def click(self, selector, settle=0.6):
        """Click nativo en el centro del elemento (Input.dispatchMouseEvent, no JS .click()
        — dispara los mismos handlers que un usuario real). Lanza si el selector no existe."""
        box = self.eval_js(
            f"(() => {{ const e = document.querySelector({selector!r}); if(!e) return null; "
            f"const r = e.getBoundingClientRect(); "
            f"return {{x: r.left + r.width/2, y: r.top + r.height/2}}; }})()")
        if not box:
            raise RuntimeError(f"CDP click: selector no encontrado: {selector}")
        for ev in ("mousePressed", "mouseReleased"):
            self._send("Input.dispatchMouseEvent", {
                "type": ev, "x": box["x"], "y": box["y"],
                "button": "left", "clickCount": 1})
        time.sleep(settle)

    def hover(self, selector, settle=0.2):
        """Mueve el puntero al centro del elemento sin click."""
        box = self.eval_js(
            f"(() => {{ const e = document.querySelector({selector!r}); if(!e) return null; "
            f"const r = e.getBoundingClientRect(); "
            f"return {{x: r.left + r.width/2, y: r.top + r.height/2}}; }})()")
        if not box:
            raise RuntimeError(f"CDP hover: selector no encontrado: {selector}")
        self._send("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": box["x"], "y": box["y"], "button": "none",
        })
        time.sleep(settle)

    def drag(self, source_selector, target_selector, settle=0.3):
        """Arrastra entre dos elementos mediante eventos de puntero reales."""
        def center(selector):
            return self.eval_js(
                f"(() => {{ const e = document.querySelector({selector!r}); if(!e) return null; "
                f"const r = e.getBoundingClientRect(); "
                f"return {{x: r.left + r.width/2, y: r.top + r.height/2}}; }})()")

        source, target = center(source_selector), center(target_selector)
        if not source or not target:
            raise RuntimeError("CDP drag: selector origen/destino no encontrado")
        self._send("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": source["x"], "y": source["y"], "button": "none",
        })
        self._send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": source["x"], "y": source["y"],
            "button": "left", "clickCount": 1,
        })
        self._send("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": target["x"], "y": target["y"],
            "button": "left", "buttons": 1,
        })
        self._send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": target["x"], "y": target["y"],
            "button": "left", "clickCount": 1,
        })
        time.sleep(settle)

    def select_option(self, selector, value):
        """Selecciona una opción y dispara input/change, compatible con formularios reactivos."""
        changed = self.eval_js(
            f"(() => {{ const e = document.querySelector({selector!r}); "
            f"if(!e || String(e.tagName).toLowerCase() !== 'select') return false; "
            f"e.value = {value!r}; "
            f"e.dispatchEvent(new Event('input', {{bubbles:true}})); "
            f"e.dispatchEvent(new Event('change', {{bubbles:true}})); return true; }})()")
        if not changed:
            raise RuntimeError(f"CDP select_option: selector/valor inválido: {selector}")

    def press_key(self, key):
        """Envía una tecla nombrada; rechaza payloads largos o no imprimibles."""
        key = str(key)
        if not key or len(key) > 32 or any(ord(ch) < 32 for ch in key):
            raise ValueError("CDP press_key: tecla inválida")
        for event in ("keyDown", "keyUp"):
            self._send("Input.dispatchKeyEvent", {"type": event, "key": key})

    def upload_file(self, selector, path):
        """Asigna un archivo local a input[type=file] mediante DOM.setFileInputFiles."""
        document = self._send("DOM.getDocument", {"depth": 0})
        node_id = int((document.get("root") or {}).get("nodeId") or 0)
        result = self._send("DOM.querySelector", {"nodeId": node_id, "selector": selector})
        target_id = int(result.get("nodeId") or 0)
        if target_id <= 0:
            raise RuntimeError(f"CDP upload_file: selector no encontrado: {selector}")
        self._send("DOM.setFileInputFiles", {"files": [str(path)], "nodeId": target_id})

    def navigate_back(self):
        """Retrocede una entrada usando el historial real de la pestaña."""
        history = self._send("Page.getNavigationHistory")
        index = int(history.get("currentIndex", 0))
        entries = history.get("entries") or []
        if index <= 0 or index >= len(entries):
            return False
        self._send("Page.navigateToHistoryEntry", {"entryId": entries[index - 1]["id"]})
        return True

    def handle_dialog(self, accept, prompt_text=""):
        """Acepta o descarta el diálogo JavaScript activo."""
        params = {"accept": bool(accept)}
        if accept and prompt_text:
            params["promptText"] = str(prompt_text)[:1000]
        self._send("Page.handleJavaScriptDialog", params)

    def type_text(self, selector, value, settle=0.3):
        """Enfoca el selector y teclea `value` carácter por carácter (Input.insertText tras
        foco real) — reproduce input de usuario, dispara eventos de React/Vue."""
        focused = self.eval_js(
            f"(() => {{ const e = document.querySelector({selector!r}); if(!e) return false; "
            f"e.focus(); return document.activeElement === e; }})()")
        if not focused:
            raise RuntimeError(f"CDP type_text: no pude enfocar: {selector}")
        self._send("Input.insertText", {"text": value})
        time.sleep(settle)

    # ── Fase 6: waits robustos · emulación · multi-pestaña · a11y · performance ──
    def wait_for(self, selector, timeout=10.0, present=True, poll=0.2):
        """Espera hasta que `selector` exista (present=True) o desaparezca (present=False),
        sondeando el DOM real. Reemplaza los sleep() frágiles: devuelve True apenas se cumple
        la condición, o False si vence el timeout. Base para automación estable."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            exists = self.eval_js(
                f"document.querySelector({selector!r}) !== null")
            if exists == present:
                return True
            time.sleep(poll)
        return False

    def wait_for_text(self, substring, timeout=10.0, poll=0.2):
        """Espera hasta que el texto visible de la página contenga `substring`."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if substring in (self.text() or ""):
                return True
            time.sleep(poll)
        return False

    def emulate_device(self, width, height, mobile=False, device_scale=1.0,
                       user_agent=None):
        """Emula un dispositivo: viewport (width×height), factor de escala y flag móvil vía
        Emulation.setDeviceMetricsOverride; opcionalmente sobreescribe el User-Agent. Sirve
        para probar layouts responsive/móvil sin hardware. clear_emulation() lo revierte."""
        self._send("Emulation.setDeviceMetricsOverride", {
            "width": int(width), "height": int(height),
            "deviceScaleFactor": float(device_scale), "mobile": bool(mobile)})
        if user_agent is not None:
            self._send("Network.enable")
            self._send("Network.setUserAgentOverride", {"userAgent": user_agent})
        return True

    def clear_emulation(self):
        """Revierte la emulación de dispositivo (vuelve al viewport nativo)."""
        self._send("Emulation.clearDeviceMetricsOverride")
        return True

    def tabs(self):
        """Lista las pestañas (targets tipo 'page') del navegador: id, url, título, si es la
        actual. Base de la gestión multi-pestaña."""
        targets = json.load(urllib.request.urlopen(
            f"http://localhost:{self.port}/json"))
        return [{"id": t.get("id"), "url": t.get("url"), "title": t.get("title")}
                for t in targets if t.get("type") == "page"]

    def open_tab(self, url="about:blank"):
        """Abre una pestaña nueva (Target.createTarget) y CAMBIA el foco a ella (la ws pasa a
        apuntar al nuevo target). Devuelve el targetId."""
        tid = self._send("Target.createTarget", {"url": url}).get("targetId")
        self._attach_target(tid)
        return tid

    def switch_tab(self, target_id):
        """Cambia el foco a una pestaña existente por su targetId (re-apunta la ws)."""
        self._attach_target(target_id)
        return target_id

    def _attach_target(self, target_id):
        """Re-apunta el websocket CDP a otro target/pestaña (para multi-tab)."""
        try:
            self.ws.close()
        except Exception:
            pass
        ws_url = f"ws://localhost:{self.port}/devtools/page/{target_id}"
        self.ws = websocket.create_connection(ws_url, suppress_origin=True)
        self._events = []

    def accessibility_tree(self, max_nodes=200):
        """Snapshot de accesibilidad: árbol AX (rol + nombre) que ve un lector de pantalla —
        distinto del DOM crudo, refleja la semántica real de la página. Devuelve lista de
        {role, name} de los nodos con nombre, recortada a max_nodes."""
        self._send("Accessibility.enable")
        nodes = self._send("Accessibility.getFullAXTree").get("nodes", [])
        out = []
        for n in nodes:
            role = (n.get("role") or {}).get("value")
            name = (n.get("name") or {}).get("value")
            if name:
                out.append({"role": role, "name": name})
            if len(out) >= max_nodes:
                break
        return out

    def accessibility_snapshot(self, max_nodes=500):
        """Devuelve el snapshot AX jerárquico completo, no una lista plana.

        Chrome puede devolver más de una raíz AX, por eso ``roots`` siempre es una lista.
        ``node_count`` cuenta los nodos materializados y ``truncated`` indica si
        ``max_nodes`` recortó el resultado. Los nodos ignorados se preservan: eliminarlos
        rompería la relación padre/hijo y ocultaría por qué el lector de pantalla omite
        una rama.
        """
        if not isinstance(max_nodes, int) or isinstance(max_nodes, bool) or max_nodes < 1:
            raise ValueError("CDP: max_nodes debe ser un entero positivo")
        self._send("Accessibility.enable")
        raw_nodes = self._send("Accessibility.getFullAXTree").get("nodes", [])
        nodes = [node for node in raw_nodes if node.get("nodeId") is not None]
        by_id = {str(node["nodeId"]): node for node in nodes}
        referenced = {
            str(child_id)
            for node in nodes
            for child_id in (node.get("childIds") or [])
            if str(child_id) in by_id
        }
        root_ids = [str(node["nodeId"]) for node in nodes
                    if str(node["nodeId"]) not in referenced]
        if not root_ids and nodes:
            # Defensa ante una respuesta CDP anómala/cíclica: conserva al menos una raíz.
            root_ids = [str(nodes[0]["nodeId"])]

        emitted = 0
        active = set()
        visited = set()

        def _value(field):
            return field.get("value") if isinstance(field, dict) else field

        def build(node_id):
            nonlocal emitted
            if emitted >= max_nodes or node_id in active or node_id in visited:
                return None
            raw = by_id.get(node_id)
            if raw is None:
                return None
            emitted += 1
            active.add(node_id)
            visited.add(node_id)
            properties = {}
            for prop in raw.get("properties") or []:
                name = prop.get("name")
                if name:
                    properties[name] = _value(prop.get("value"))
            item = {
                "node_id": node_id,
                "role": _value(raw.get("role")),
                "name": _value(raw.get("name")),
                "ignored": bool(raw.get("ignored", False)),
                "properties": properties,
                "children": [],
            }
            for child_id in raw.get("childIds") or []:
                child = build(str(child_id))
                if child is not None:
                    item["children"].append(child)
                if emitted >= max_nodes:
                    break
            active.remove(node_id)
            return item

        roots = []
        for root_id in root_ids:
            root = build(root_id)
            if root is not None:
                roots.append(root)
            if emitted >= max_nodes:
                break
        return {
            "roots": roots,
            "node_count": emitted,
            "total_nodes": len(nodes),
            "truncated": emitted < len(nodes),
        }

    def performance_metrics(self):
        """Métricas de performance de la página (Performance.getMetrics): tiempos, nodos,
        layouts, uso de JS heap, etc. Devuelve dict {nombre: valor}."""
        self._send("Performance.enable")
        metrics = self._send("Performance.getMetrics").get("metrics", [])
        return {m.get("name"): m.get("value") for m in metrics}

    def start_trace(self, categories=None, screenshots=False):
        """Inicia un trace DevTools completo; stop_trace() lo persiste como JSON.

        A diferencia de performance_metrics(), conserva la línea temporal de eventos.
        No incluye cuerpos de red ni cookies. ``screenshots=True`` agrega frames al trace.
        """
        if self._tracing:
            raise RuntimeError("CDP trace: ya hay un trace activo")
        self._events = [event for event in self._events
                        if not str(event.get("method", "")).startswith("Tracing.")]
        cats = list(categories or (
            "devtools.timeline",
            "disabled-by-default-devtools.timeline",
            "blink.user_timing",
            "v8.execute",
        ))
        if screenshots:
            cats.append("disabled-by-default-devtools.screenshot")
        self._send("Tracing.start", {
            "categories": ",".join(dict.fromkeys(cats)),
            "transferMode": "ReturnAsStream",
        })
        self._tracing = True
        self._audit("trace_start", screenshots=bool(screenshots), categories=cats)
        return True

    def stop_trace(self, path, timeout=20.0):
        """Finaliza el trace activo y escribe el JSON completo en ``path``."""
        if not self._tracing:
            raise RuntimeError("CDP trace: no hay trace activo")
        self._send("Tracing.end")
        completed = self._wait_for_event("Tracing.tracingComplete", timeout=timeout)
        stream = (completed.get("params") or {}).get("stream")
        if not stream:
            self._tracing = False
            raise RuntimeError("CDP trace: Tracing.tracingComplete no devolvio stream")
        import base64
        chunks = []
        try:
            while True:
                part = self._send("IO.read", {"handle": stream})
                data = part.get("data", "")
                chunks.append(base64.b64decode(data) if part.get("base64Encoded")
                              else data.encode("utf-8"))
                if part.get("eof"):
                    break
        finally:
            try:
                self._send("IO.close", {"handle": stream})
            except Exception:
                pass
            self._tracing = False
        payload = b"".join(chunks)
        # Fail-fast: no declarar trace si Chrome devolvió un stream truncado/no JSON.
        parsed = json.loads(payload.decode("utf-8"))
        if not isinstance(parsed, dict) or not isinstance(parsed.get("traceEvents"), list):
            raise RuntimeError("CDP trace: formato inesperado")
        path = os.path.abspath(os.path.expanduser(str(path)))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(payload)
        self._audit("trace_stop", path=path, bytes=len(payload),
                    events=len(parsed["traceEvents"]))
        return path

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._tracing:
            try:
                self._send("Tracing.end")
            except Exception:
                pass
            self._tracing = False
        try:
            self.ws.close()
        except Exception:
            pass
        if self._proc:
            # Matar TODO el grupo (navegador + renderers). terminate() solo mataba el padre
            # y dejaba renderers zombie que se acumulaban corrida tras corrida.
            try:
                pgid = os.getpgid(self._proc.pid)
            except (ProcessLookupError, PermissionError):
                pgid = None
            try:
                if pgid is not None:
                    os.killpg(pgid, signal.SIGTERM)
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    if pgid is not None:
                        os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                try:
                    self._proc.wait(timeout=2)
                except Exception:
                    pass
            # ``Popen.wait`` solo espera al browser padre. Los renderers del mismo
            # process group pueden tardar unos cientos de ms más; esperar la extinción
            # del grupo vuelve verificable el contrato de cierre del árbol completo.
            if pgid is not None:
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    try:
                        os.killpg(pgid, 0)
                    except ProcessLookupError:
                        break
                    except PermissionError:
                        break
                    time.sleep(0.05)
                else:
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                    deadline = time.monotonic() + 1.0
                    while time.monotonic() < deadline:
                        try:
                            os.killpg(pgid, 0)
                        except (ProcessLookupError, PermissionError):
                            break
                        time.sleep(0.05)
            self._proc = None
        if self._own_profile:
            shutil.rmtree(self._own_profile, ignore_errors=True)
            self._own_profile = None
        if self._egress_proxy is not None:
            self._egress_proxy.close()
            self._egress_proxy = None
            self._proxy_username = ""
            self._proxy_password = ""


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765/"
    b = CDP()
    try:
        b.navigate(target)
        print(f"=== network requests para {target} ===")
        for r in b.network_requests():
            print(f"  {r['status']}  {r['mime']:<24} {r['url'][:80]}")
    finally:
        b.close()
