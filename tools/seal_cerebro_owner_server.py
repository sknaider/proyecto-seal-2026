#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seal_cerebro_owner_server.py — Visor cerebro de DOS tiers (NEXUS #23).
================================================================================
Reemplaza el allowlist-server público por uno de DOS tiers sobre el MISMO puerto:

  TIER PÚBLICO (sin auth) — sirve el dir PÚBLICO REDACTADO (allowlist):
    GET /                 → 302 /viewer_3d.html
    GET /viewer_3d.html   → público redactado
    GET /graph.json       → público redactado

  TIER DUEÑO (auth) — sirve el dir PRIVADO FULL (contenido real), SOLO con sesión válida:
    GET  /owner            → si sesión válida: 302 /owner/viewer_3d.html ; si no: 302 /owner/login
    GET  /owner/login      → formulario mínimo de login
    POST /owner/login      → passphrase → verifica hash → set cookie firmada (HttpOnly) → 302 /owner/viewer_3d.html
    GET  /owner/viewer_3d.html , /owner/graph.json → SOLO con cookie válida → dir FULL ; si no → 302 login
    GET  /owner/logout     → borra cookie

PRINCIPIO (#19 RLS): la data FULL NUNCA se sirve sin verificación server-side de la
cookie firmada. El dir FULL está FUERA del árbol público; un path público no lo alcanza.
La autoridad se deriva de la firma HMAC de la cookie (server-side), no de input del cliente.

Cookie = base64url(json{sub,exp}) + "." + HMAC-SHA256(secret, payload). Sin dep externa.
Secret desde archivo chmod 600 (no hardcodeado). Passphrase verificada contra hash scrypt.

Anti-fuerza-bruta: backoff incremental por IP en /owner/login.
Anti-traversal: allowlist EXACTA de basenames por tier; nunca os.path.join con input crudo.

Config por env:
  SEAL_CEREBRO_PUBLIC_DIR   dir público redactado (default: memory/diagnostic/soul_cognitive_graph_view)
  SEAL_CEREBRO_OWNER_DIR    dir privado full     (default: ~/.config/seal/cerebro_owner)
  SEAL_CEREBRO_SECRET_FILE  archivo del secreto HMAC (default: ~/.config/seal/cerebro_owner.secret)
  SEAL_CEREBRO_CRED_FILE    archivo del hash de passphrase (default: ~/.config/seal/cerebro_owner.cred)
  SEAL_CEREBRO_PORT (8799)  SEAL_CEREBRO_BIND (0.0.0.0)  SEAL_CEREBRO_TTL_MIN (60)
"""
from __future__ import annotations
import os, sys, json, hmac, hashlib, base64, time, html, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

HOME = os.path.expanduser("~")
PUBLIC_DIR = os.path.realpath(os.environ.get("SEAL_CEREBRO_PUBLIC_DIR",
    os.path.join(os.path.dirname(__file__), "..", "memory/diagnostic/soul_cognitive_graph_view")))
OWNER_DIR = os.path.realpath(os.environ.get("SEAL_CEREBRO_OWNER_DIR", os.path.join(HOME, ".config/seal/cerebro_owner")))
SECRET_FILE = os.environ.get("SEAL_CEREBRO_SECRET_FILE", os.path.join(HOME, ".config/seal/cerebro_owner.secret"))
CRED_FILE = os.environ.get("SEAL_CEREBRO_CRED_FILE", os.path.join(HOME, ".config/seal/cerebro_owner.cred"))
TTL = int(os.environ.get("SEAL_CEREBRO_TTL_MIN", "60")) * 60

PUBLIC_ALLOW = {"/viewer_3d.html", "/graph.json"}
OWNER_ALLOW = {"viewer_3d.html", "graph.json"}      # basenames servibles del dir full
_MIME = {".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8"}
COOKIE = "cerebro_owner_session"

_fail = {}            # ip -> (count, next_allowed_ts)  (anti-fuerza-bruta)
_lock = threading.Lock()


def _b64u(b: bytes) -> str: return base64.urlsafe_b64encode(b).decode().rstrip("=")
def _b64u_dec(s: str) -> bytes: return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _secret() -> bytes:
    with open(SECRET_FILE, "rb") as f:
        return f.read().strip()


def _sign(payload: dict) -> str:
    body = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _verify(token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
        expected = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):   # firma inválida/manipulada
            return None
        payload = json.loads(_b64u_dec(body))
        if int(payload.get("exp", 0)) < int(time.time()):   # expirada
            return None
        return payload
    except Exception:
        return None


def _check_passphrase(passphrase: str) -> bool:
    """Verifica passphrase contra hash scrypt almacenado (formato: salt_hex:hash_hex)."""
    try:
        with open(CRED_FILE) as f:
            salt_hex, hash_hex = f.read().strip().split(":", 1)
        salt = bytes.fromhex(salt_hex)
        cand = hashlib.scrypt(passphrase.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(cand.hex(), hash_hex)
    except Exception:
        return False


_LOGIN_HTML = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>SOUL — Vista Dueño</title><style>body{{background:#111318;color:#e8edf4;font-family:system-ui;
display:flex;height:100vh;align-items:center;justify-content:center;margin:0}}
form{{background:#1b2028;padding:32px;border-radius:12px;border:1px solid #313946;width:320px}}
input{{width:100%;padding:10px;margin:8px 0;background:#111318;border:1px solid #313946;color:#e8edf4;border-radius:6px}}
button{{width:100%;padding:10px;background:#8fb9ff;color:#111;border:0;border-radius:6px;font-weight:600;cursor:pointer}}
.m{{color:#f28f8f;font-size:13px;min-height:18px}}</style></head><body>
<form method="POST" action="/owner/login"><h3>🧠 Cerebro SOUL — Vista Dueño</h3>
<div class="m">{msg}</div><input type="password" name="passphrase" placeholder="Passphrase SOUL" autofocus>
<button type="submit">Entrar</button></form></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "SEALCerebro/2.0"

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}):
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _redirect(self, loc, headers=None):
        self.send_response(302); self.send_header("Location", loc)
        for k, v in (headers or []):
            self.send_header(k, v)
        self.end_headers()

    def _serve_file(self, root, basename):
        full = os.path.realpath(os.path.join(root, basename))
        if not full.startswith(os.path.realpath(root) + os.sep) or not os.path.isfile(full):
            self._send(404, b"404\n"); return
        with open(full, "rb") as f:
            data = f.read()
        self._send(200, data, _MIME.get(os.path.splitext(full)[1].lower(), "application/octet-stream"))

    def _session(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            if part.strip().startswith(COOKIE + "="):
                return _verify(part.strip()[len(COOKIE) + 1:])
        return None

    def _client_ip(self):
        return self.client_address[0] if self.client_address else "?"

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        # ── TIER PÚBLICO ──
        if path == "/":
            self._redirect("/viewer_3d.html"); return
        if path in PUBLIC_ALLOW:
            self._serve_file(PUBLIC_DIR, path.lstrip("/")); return
        # ── TIER DUEÑO ──
        if path == "/owner" or path == "/owner/":
            self._redirect("/owner/viewer_3d.html" if self._session() else "/owner/login"); return
        if path == "/owner/login":
            self._send(200, _LOGIN_HTML.format(msg="").encode(), "text/html; charset=utf-8"); return
        if path == "/owner/logout":
            self._redirect("/owner/login", [("Set-Cookie", f"{COOKIE}=; Path=/owner; Max-Age=0; HttpOnly; SameSite=Strict")]); return
        if path.startswith("/owner/"):
            base = path[len("/owner/"):]
            if base not in OWNER_ALLOW:
                self._send(404, b"404\n"); return
            if not self._session():                      # GATE: sin sesión válida → NUNCA sirve full
                self._redirect("/owner/login"); return
            self._serve_file(OWNER_DIR, base); return
        self._send(404, b"404\n")

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/owner/login":
            self._send(404, b"404\n"); return
        ip = self._client_ip()
        with _lock:
            cnt, nxt = _fail.get(ip, (0, 0))
            if time.time() < nxt:
                self._send(200, _LOGIN_HTML.format(msg="Demasiados intentos, esperá.").encode(), "text/html; charset=utf-8"); return
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(min(length, 4096)).decode("utf-8", "replace")
        passphrase = (parse_qs(body).get("passphrase", [""])[0]).strip()  # tolera whitespace/newline pegado (JARVIS)
        if _check_passphrase(passphrase):
            with _lock:
                _fail.pop(ip, None)
            token = _sign({"sub": "william", "role": "owner", "exp": int(time.time()) + TTL})
            self._redirect("/owner/viewer_3d.html",
                           [("Set-Cookie", f"{COOKIE}={token}; Path=/owner; Max-Age={TTL}; HttpOnly; SameSite=Strict")])
        else:
            with _lock:
                cnt = cnt + 1
                _fail[ip] = (cnt, time.time() + min(2 ** cnt, 60))   # backoff exponencial cap 60s
            self._send(200, _LOGIN_HTML.format(msg="Passphrase incorrecta.").encode(), "text/html; charset=utf-8")

    def log_message(self, *a):
        return


def main():
    for req in (PUBLIC_DIR,):
        if not os.path.isdir(req):
            print(f"[cerebro] FALTA dir público: {req}", file=sys.stderr); sys.exit(1)
    if not os.path.isfile(SECRET_FILE) or not os.path.isfile(CRED_FILE):
        print(f"[cerebro] FALTA secret/cred. Corré primero: seal_cerebro_owner_server.py --setup", file=sys.stderr); sys.exit(1)
    port = int(os.environ.get("SEAL_CEREBRO_PORT", "8799"))
    bind = os.environ.get("SEAL_CEREBRO_BIND", "0.0.0.0")
    httpd = ThreadingHTTPServer((bind, port), Handler)
    print(f"[cerebro] público={PUBLIC_DIR} dueño={OWNER_DIR} en {bind}:{port}", flush=True)
    httpd.serve_forever()


def setup():
    """Crea secret HMAC + pide passphrase por STDIN (NUNCA por chat) y guarda su hash. chmod 600."""
    import getpass
    os.makedirs(os.path.dirname(SECRET_FILE), exist_ok=True)
    if not os.path.isfile(SECRET_FILE):
        with open(os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
            f.write(base64.urlsafe_b64encode(os.urandom(48)))
        print(f"[setup] secret HMAC creado: {SECRET_FILE}")
    p1 = getpass.getpass("Nueva passphrase dueño: ")
    if len(p1) < 8:
        print("[setup] mínimo 8 caracteres."); sys.exit(1)
    if p1 != getpass.getpass("Repetí: "):
        print("[setup] no coinciden."); sys.exit(1)
    salt = os.urandom(16)
    h = hashlib.scrypt(p1.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    with open(os.open(CRED_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(f"{salt.hex()}:{h.hex()}")
    print(f"[setup] passphrase hasheada (scrypt) guardada: {CRED_FILE}")


if __name__ == "__main__":
    if "--setup" in sys.argv:
        setup()
    else:
        main()
