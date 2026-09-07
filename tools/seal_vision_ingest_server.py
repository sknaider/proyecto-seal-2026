"""SOUL Vision — endpoint de INGESTA seguro (lane NEXUS).

Recibe el PUSH unidireccional del 5070 (push_to_soul.push_memory) y persiste el
recuerdo vision-scope en soul_v3.memories del Spark. Es el lado-Spark del puente.

SEGURIDAD (por qué existe separado del chat 8765 crítico):
  - Servicio AISLADO en su propio puerto → un bug aquí NO tumba la mensajería del equipo.
  - Token obligatorio (X-SEAL-Vision-Token) leído de ~/.config/seal/vision_ingest_token (chmod 600).
  - Allowlist opcional de IP (la tailnet del 5070) → SEAL_VISION_ALLOW_IP.
  - scope FORZADO a 'vision' + agent FIJO 'SOUL_Vision' → nadie puede inyectar otro scope/agente
    ni contaminar memorias de la familia.
  - importance ACOTADO a <=6 → nunca crea memoria INTOCABLE (>=7), regla de William.
  - Idempotente por content_hash_sha256 (metadata) → reintentos no duplican.
  - Sólo ESCRIBE (INSERT). No expone ninguna ruta de lectura del Sanctum (push-no-pull).

Env:
  SEAL_VISION_INGEST_BIND   (default 127.0.0.1; en prod: IP tailnet del Spark)
  SEAL_VISION_INGEST_PORT   (default 8770)
  SEAL_VISION_ALLOW_IP      (opcional; IP del 5070 — si se setea, rechaza el resto)
  SOUL_MEMORY_DSN           (default: DSN soul_v3 del Spark)
"""
from __future__ import annotations
import os, json, uuid, asyncio, hmac, ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_DEFAULT_DSN = "postgresql://seal:seal@localhost:5432/soul"  # alineado con seal/memory.py _DEFAULT_DSN
DSN = os.environ.get("SOUL_MEMORY_DSN", _DEFAULT_DSN)
BIND = os.environ.get("SEAL_VISION_INGEST_BIND", "127.0.0.1")
PORT = int(os.environ.get("SEAL_VISION_INGEST_PORT", "8770"))
ALLOW_IP = os.environ.get("SEAL_VISION_ALLOW_IP", "").strip()
TOKEN_PATH = os.path.expanduser("~/.config/seal/vision_ingest_token")

_AGENT = "SOUL_Vision"          # fijo: nadie suplanta otro agente
_SCOPE = "vision"              # forzado: no puede escribir otro scope
_CATEGORY = "vision_event"
_MAX_IMPORTANCE = 6            # nunca INTOCABLE (>=7)
_MAX_CONTENT = 4000           # cap anti-abuso


def _load_token() -> str:
    try:
        with open(TOKEN_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


async def _insert(content: str, importance: int, meta: dict) -> str | None:
    import asyncpg
    conn = await asyncpg.connect(DSN, timeout=8)
    try:
        # idempotencia: si ya existe ese content_hash en metadata vision, no duplica
        ch = meta.get("content_hash_sha256")
        if ch:
            existing = await conn.fetchval(
                "SELECT id FROM soul_v3.memories WHERE agent=$1 AND scope=$2 "
                "AND metadata->>'content_hash_sha256' = $3 LIMIT 1",
                _AGENT, _SCOPE, ch)
            if existing:
                return None  # deduped
        mid = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO soul_v3.memories
                (id, agent, scope, category, content, importance, source_tier, metadata, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, 'vision_push', $7::jsonb, NOW())
            """,
            mid, _AGENT, _SCOPE, _CATEGORY, content, importance, json.dumps(meta))
        return mid
    finally:
        await conn.close()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silenciar log ruidoso por defecto
        pass

    def do_GET(self):
        if self.path == "/api/vision/health":
            self._send(200, {"ok": True, "service": "vision-ingest"})
        else:
            self._send(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        if self.path != "/api/vision/ingest":
            return self._send(404, {"ok": False, "error": "not_found"})
        # 1) allowlist IP (si configurada)
        if ALLOW_IP:
            peer = self.client_address[0]
            try:
                if ipaddress.ip_address(peer) != ipaddress.ip_address(ALLOW_IP):
                    return self._send(403, {"ok": False, "error": "ip_not_allowed"})
            except ValueError:
                return self._send(403, {"ok": False, "error": "bad_ip"})
        # 2) token (comparación en tiempo constante)
        tok = _load_token()
        got = self.headers.get("X-SEAL-Vision-Token", "")
        if not tok or not got or not hmac.compare_digest(tok, got):
            return self._send(401, {"ok": False, "error": "unauthorized"})
        # 3) body
        try:
            n = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            return self._send(400, {"ok": False, "error": "bad_json"})
        content = str(data.get("content", "")).strip()[:_MAX_CONTENT]
        if not content:
            return self._send(400, {"ok": False, "error": "empty_content"})
        importance = min(max(int(data.get("importance", 4) or 4), 1), _MAX_IMPORTANCE)
        # metadata curada: SOLO campos vision-scope; scope/agent NO vienen del cliente
        meta = {
            "content_hash_sha256": data.get("content_hash_sha256"),
            "obj_classes": data.get("obj_classes"),
            "camera_id": data.get("camera_id"),
            "confidence": data.get("confidence"),
            "origin": "soul_vision_5070_push",
        }
        try:
            mid = asyncio.run(_insert(content, importance, meta))
        except Exception as e:
            return self._send(502, {"ok": False, "error": f"db: {type(e).__name__}"})
        if mid is None:
            return self._send(200, {"ok": True, "deduped": True})
        return self._send(200, {"ok": True, "memory_id": mid})


def main():
    if not _load_token():
        print(f"[vision-ingest] WARN: no token en {TOKEN_PATH} — todas las requests darán 401 hasta crearlo")
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"[vision-ingest] escuchando en {BIND}:{PORT} (allow_ip={ALLOW_IP or 'any'}) → soul_v3.memories scope=vision")
    srv.serve_forever()


if __name__ == "__main__":
    main()
