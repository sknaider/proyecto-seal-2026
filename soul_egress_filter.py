#!/usr/bin/env python3
"""
soul_egress_filter.py — Filtro de minimización de datos en el EGRESO hacia Anthropic.

Objetivo (William 15-jun-2026): "lo mínimo de información enviar a servidores —
un filtro para que llegue lo necesario". Que secretos / PII / memorias íntimas /
payload NO salgan en claro hacia api.anthropic.com, aunque el turno sí se procese.

Arquitectura — proxy de redacción TRANSPARENTE:
  Claude Code  --(ANTHROPIC_BASE_URL=http://localhost:9098)-->  ESTE proxy
       -> redacta VALORES sensibles (deny-list + patrones), preservando estructura
          (system, messages, content blocks, tool_use/tool_result, cache_control)
       -> reenvía a https://api.anthropic.com con las MISMAS cabeceras de auth
          (el proxy es auth-transparente; nunca ve/almacena el token salvo passthrough)
       -> stream SSE de vuelta, re-hidratando tokens ⟦SOUL:..⟧ si el modelo los repite.

DISEÑO DE SEGURIDAD (doctrina NEXUS):
  - Deny-list driven (no allow-all): solo redacta lo que matchea; el resto pasa intacto
    para NO romper el razonamiento del agente (minimización = sacar valores sensibles,
    no vaciar contexto).
  - Tokenización DETERMINISTA por valor → re-hidratación correcta y consistente intra-request.
  - El mapa token→valor vive SOLO en memoria del proceso, por-request, y se descarta al cerrar.
  - CANARIO: corre en :9098, cableado a NADA. Se prueba con un agente antes de tocar al resto.

Estado: CANARIO / no cableado. Verificación: `python3 soul_egress_filter.py --selftest`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys

# Deps del modo proxy a NIVEL DE MÓDULO (no en main()): con `from __future__ import
# annotations` las anotaciones son forward-refs (strings); FastAPI/pydantic resuelven
# `Request` contra los globals del módulo → si se importa local a main() NO resuelve
# (causa: PydanticUserError 'Request not fully defined' → 422 query.request). Guardado
# para que --selftest funcione aunque falten las libs del servidor.
try:
    import httpx
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import StreamingResponse, JSONResponse
except ImportError:
    httpx = uvicorn = FastAPI = Request = StreamingResponse = JSONResponse = None

# ── Deny-list de patrones sensibles ──────────────────────────────────────────
# JARVIS aporta el mapa de sensibilidad; ALICE la deny-list fina. Esto es la base
# extensible. Cada patrón → razón (para auditoría). Orden: específico antes que genérico.
SENSITIVE_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("anthropic_key",  re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key",     re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("oauth_token",    re.compile(r"(?i)\b(?:bearer|oauth|access[_-]?token)\s*[:=]?\s*[A-Za-z0-9._\-]{20,}")),
    ("pg_dsn",         re.compile(r"postg(?:res|resql)://[^\s\"']+:[^\s\"'@]+@[^\s\"']+")),
    ("aws_key",        re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key",    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("email",          re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("jwt",            re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
]

# Patrones TIER-1 (reversibles) — topología/IDs internos (taxonomía JARVIS / política ALICE).
SENSITIVE_PATTERNS += [
    ("private_ip",  re.compile(r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|100\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")),
    ("invoice_id",  re.compile(r"\bF\d{3}-\d{6,8}\b")),
    # Anti fail-open (review JARVIS): secreto de ALTA ENTROPÍA sin prefijo conocido —
    # hex/base64 largo = casi seguro una llave/hash/token. Caza la llave maestra (40 hex)
    # que el regex estructurado no veía.
    ("hi_entropy_hex", re.compile(r"\b[0-9a-fA-F]{32,}\b")),
    ("hi_entropy_b64", re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b")),
    # Cierre de fugas cazadas por los canarios TIER-0 de ALICE:
    ("google_oauth",   re.compile(r"GOCSPX-[A-Za-z0-9_\-]+")),
    ("client_secret",  re.compile(r"(?i)client[_-]?secret[\"'\s:=]+[A-Za-z0-9._\-]{8,}")),
    ("ruc_pe",         re.compile(r"\b[12]0[A-Za-z0-9]{9}\b")),
    ("dni_pe",         re.compile(r"(?i)\bDNI[=:\s]*[A-Za-z0-9]{8,}\b")),
    ("monto_soles",    re.compile(r"S/\.?\s*[\d.,]+")),
]

# Bloque de tag privado de SOUL: cualquier [[...]] → contenido privado (Sanctum/vault/
# SPECTRE-raw). Convención estructural; redacta el TEXTO ENTERO que lo contiene.
PRIVATE_BRACKET = re.compile(r"\[\[[^\]]+\]\]")

# Literales sensibles conocidos (infra). Se cargan desde config externa / runtime.
# NO incrustar secretos reales en este archivo. load_sensitive_config() los puebla.
SENSITIVE_LITERALS: set[str] = set()

# Marcadores de contenido privado de SOUL. Si un bloque de texto los contiene,
# se redacta el bloque ENTERO (no solo un patrón) — son intimidad de la familia.
# TIER-0 (hard-block) de la política canónica de ALICE + taxonomía de JARVIS.
PRIVATE_MARKERS: tuple[str, ...] = (
    "[[PRIVATE]]", "channel=spectre", "from=SPECTRE", "[DM]",
    "scope=private", "scope=vault", "[VAULT]", "diario emocional",
    "seal_tokens",
    # Estructuras PII/financiero/host → línea entera sensible (específicos con '='/IP
    # para no sobre-matchear texto benigno).
    "nombre=", "apellido=", "DNI=", "DNI:", "RUC=", "RUC:", "monto=", "192.168.",
)


DENYLIST_PATH = "/home/dadito/IA/proyecto-seal/soul_egress_denylist.txt"


def load_sensitive_config(extra_path: str | None = None) -> int:
    """Carga literales TIER-0 que JAMÁS deben egresar: identity/authority tokens
    (`/tmp/seal_tokens/*`) y la deny-list de secretos (DENYLIST_PATH, chmod 600,
    gitignored). Determinista, local, al boot. Es la ÚNICA garantía para secretos
    CORTOS/numéricos que la entropía no caza (review JARVIS #1b). Devuelve cuántos
    literales cargó. NUNCA loguea los valores."""
    import glob
    import os
    n = 0
    for path in glob.glob("/tmp/seal_tokens/*"):
        try:
            if os.path.isfile(path):
                val = open(path, encoding="utf-8", errors="replace").read().strip()
                if val and len(val) >= 6:
                    SENSITIVE_LITERALS.add(val)
                    n += 1
        except Exception:
            continue
    for src in (DENYLIST_PATH, "/home/dadito/IA/proyecto-seal/soul_egress_terms.txt", extra_path):
        if not src:
            continue
        try:
            if os.path.isfile(src):
                for line in open(src, encoding="utf-8", errors="replace"):
                    line = line.strip()
                    if line and not line.startswith("#") and len(line) >= 4:
                        SENSITIVE_LITERALS.add(line)
                        n += 1
        except Exception:
            continue
    return n

TOKEN_PREFIX = "⟦SOUL:"   # ⟦SOUL:
TOKEN_SUFFIX = "⟧"        # ⟧

# Headers de retry/rate-limit que Claude Code lee para decidir reintentos y backoff
# (SDK anthropic los consulta por nombre exacto). Si el proxy los descarta, el cliente
# reintenta a ciegas o ignora el rate-limit real del upstream — gap pedido por ADA 18-jul.
_FORWARD_HEADER_PREFIXES = ("anthropic-ratelimit-",)
_FORWARD_HEADER_NAMES = {"request-id", "x-should-retry", "retry-after"}


def _forwardable_response_headers(upstream_headers) -> dict[str, str]:
    out = {}
    for k, v in upstream_headers.items():
        lk = k.lower()
        if lk in _FORWARD_HEADER_NAMES or lk.startswith(_FORWARD_HEADER_PREFIXES):
            out[k] = v
    return out


def _token_for(value: str) -> str:
    h = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{TOKEN_PREFIX}{h}{TOKEN_SUFFIX}"


class Redactor:
    """Redacta valores sensibles y guarda el mapa token→valor para re-hidratar."""

    def __init__(self) -> None:
        self.map: dict[str, str] = {}   # token -> valor original
        self.hits: list[str] = []       # razones (auditoría, sin el valor)

    def _store(self, value: str, reason: str) -> str:
        tok = _token_for(value)
        self.map[tok] = value
        self.hits.append(reason)
        return tok

    def redact_text(self, text: str) -> str:
        if not text:
            return text
        # 1) bloque privado entero — markers + convención de tag [[...]] de SOUL
        for marker in PRIVATE_MARKERS:
            if marker in text:
                return self._store(text, f"private_block:{marker}")
        if PRIVATE_BRACKET.search(text):
            return self._store(text, "private_block:[[bracket]]")
        # 2) literales conocidos
        for lit in SENSITIVE_LITERALS:
            if lit and lit in text:
                text = text.replace(lit, self._store(lit, "literal"))
        # 3) patrones
        for reason, pat in SENSITIVE_PATTERNS:
            def _sub(m: "re.Match[str]") -> str:
                return self._store(m.group(0), reason)
            text = pat.sub(_sub, text)
        return text

    def rehydrate(self, text: str) -> str:
        if not text or TOKEN_PREFIX not in text:
            return text
        for tok, val in self.map.items():
            if tok in text:
                text = text.replace(tok, val)
        return text


# Bloques FIRMADOS/estructurales de Claude que NO se deben tocar (romperían la firma
# o el protocolo): el thinking lleva una `signature` criptográfica sobre su contenido.
SKIP_BLOCK_TYPES = {"thinking", "redacted_thinking"}
# Claves cuyos valores son estructurales (firmas, ids, tipos) — NUNCA redactar:
# redactarlas corrompe el request (caso real: 400 Invalid signature in thinking block).
NEVER_REDACT_KEYS = {
    "signature", "type", "id", "tool_use_id", "name", "model", "role",
    "cache_control", "anthropic-version", "stop_reason", "stop_sequence",
}


def _redact_schema_descriptions(obj, red: Redactor):
    """Recorre un JSON Schema (input_schema de una tool) redactando SOLO los campos
    `description` — texto libre que puede llevar secretos, igual que el resto del
    prompt. Deliberadamente NO toca `enum`/`const`/`default`/`type`: son valores
    ESTRUCTURALES que definen el contrato de la tool; tokenizarlos rompe el schema
    y, en respuestas no-streaming, un token ⟦SOUL:..⟧ puede volver sin rehidratar
    (la rehidratación no-stream solo cubre content[].text) — gap cazado por ADA
    18-jul tras mi primer intento de redactar el árbol completo con _redact_leaves."""
    if isinstance(obj, dict):
        return {k: (red.redact_text(v) if k == "description" and isinstance(v, str)
                     else _redact_schema_descriptions(v, red))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_schema_descriptions(x, red) for x in obj]
    return obj


def _redact_leaves(obj, red: Redactor):
    """Redacta TODOS los valores string de una sub-estructura (para tool_use.input:
    los argumentos de una tool-call — command, file_path, content... — pueden llevar
    secretos/DSN/payload que SÍ deben tacharse antes de egresar). Gap cazado por JARVIS."""
    if isinstance(obj, dict):
        return {k: (v if k in NEVER_REDACT_KEYS else _redact_leaves(v, red))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_leaves(x, red) for x in obj]
    if isinstance(obj, str):
        return red.redact_text(obj)
    return obj


def walk_redact(obj, red: Redactor):
    """Recorre el JSON del request Anthropic y redacta el texto del prompt (valores de
    `text`/`content`-string y los argumentos de `tool_use.input`), preservando estructura,
    bloques firmados (thinking) y campos estructurales (signature/id/type/...)."""
    if isinstance(obj, dict):
        _t = obj.get("type")
        if isinstance(_t, str) and _t in SKIP_BLOCK_TYPES:
            return obj  # bloque firmado: intacto
        is_tool_use = (_t == "tool_use")
        out = {}
        for k, v in obj.items():
            if k in NEVER_REDACT_KEYS:
                out[k] = v
            elif is_tool_use and k == "input":
                out[k] = _redact_leaves(v, red)   # argumentos de tool-call: tachar valores
            elif k in ("text", "content", "system") and isinstance(v, str):
                # `system` como string plano (no lista de blocks) es el MISMO prompt de
                # sistema que sí se redacta cuando viene en forma de bloques — gap hallado
                # por ADA (auditoría 18-jul, count_tokens con `system` string en claro).
                out[k] = red.redact_text(v)
            elif k == "description" and isinstance(v, str):
                # tools[].description / input_schema.description: texto libre que puede
                # llevar el mismo tipo de secretos que el prompt (gap ADA 18-jul).
                out[k] = red.redact_text(v)
            elif k == "input_schema":
                # SOLO descripciones — enum/const/default/type quedan intactos (ADA 18-jul,
                # ver docstring de _redact_schema_descriptions).
                out[k] = _redact_schema_descriptions(v, red)
            else:
                out[k] = walk_redact(v, red)
        return out
    if isinstance(obj, list):
        return [walk_redact(x, red) for x in obj]
    # strings sueltos NO se redactan por defecto (evita corromper ids/firmas/tokens
    # estructurales). El texto sensible vive bajo `text`/`content`/`tool_use.input`.
    return obj


# ── Self-test (sin red): demuestra redacción fiel + re-hidratación ───────────
def selftest() -> int:
    sample = {
        "model": "claude-opus-4-8",
        "system": [{"type": "text", "text": "Eres NEXUS. DB postgresql://seal:REDACTADO@localhost:5433/seal_memory",
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "Mi email es williamtovaru@gmail.com y el token sk-ant-ABCDEF1234567890ZZZZ."},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "run", "input": {"cmd": "echo hi"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": [{"type": "text", "text": "[[PRIVATE]] memoria íntima de la familia"}]},
            ]},
        ],
        "tools": [{"name": "run", "input_schema": {"type": "object"}}],
    }
    red = Redactor()
    out = walk_redact(sample, red)
    flat = json.dumps(out, ensure_ascii=False)

    checks = {
        "DSN redactado": "seal_memory_2026" not in flat,
        "email redactado": "williamtovaru@gmail.com" not in flat,
        "anthropic key redactada": "sk-ant-ABCDEF1234567890ZZZZ" not in flat,
        "bloque privado redactado": "memoria íntima" not in flat,
        "estructura preservada (tool_use)": out["messages"][1]["content"][0]["type"] == "tool_use",
        "cache_control preservado": out["system"][0].get("cache_control", {}).get("type") == "ephemeral",
        "tool schema intacto": out["tools"][0]["name"] == "run",
        "token presente": TOKEN_PREFIX in flat,
    }
    # re-hidratación: el modelo "repite" un token → debe volver el valor real localmente
    a_token = next(iter(red.map))
    rehyd = red.rehydrate(f"el valor era {a_token}")
    checks["re-hidratación correcta"] = red.map[a_token] in rehyd

    # ── CASOS ADVERSARIALES (review JARVIS #3): secretos SIN patrón estructurado ──
    # Llave maestra hex 40 chars (sin prefijo sk-/postgres) — el fail-open la dejaba salir.
    red_adv = Redactor()
    master = "a3f9" + "0" * 32 + "bc"   # 38 hex chars, sin prefijo conocido
    out_master = json.dumps(walk_redact({"x": {"text": f"key={master}"}}, red_adv), ensure_ascii=False)
    checks["[adv] llave hex alta-entropía redactada"] = master not in out_master

    # Literal CARGADO DEL CONFIG REAL (fix review JARVIS #1b — NO auto-inyectar).
    # El selftest exige que load_sensitive_config() haya poblado el denylist real;
    # si el denylist está vacío, este check FALLA (fail-closed, no verde falso).
    loaded = load_sensitive_config()
    red_lit = Redactor()
    out_lit = json.dumps(walk_redact({"x": {"text": "pass del spark: 42478340 ok"}}, red_lit), ensure_ascii=False)
    checks[f"[adv] literal de secret-store redactado (cargó {loaded} via config real)"] = (
        "42478340" not in out_lit
    )

    # Negativo de control: texto benigno NO debe redactarse (evita over-redaction que rompe al agente).
    red_neg = Redactor()
    benign = "Refactoriza la función parse_invoice y corre los tests."
    out_neg = json.dumps(walk_redact({"x": {"text": benign}}, red_neg), ensure_ascii=False)
    checks["[adv] texto benigno intacto (no over-redact)"] = "parse_invoice" in out_neg and len(red_neg.map) == 0

    ok = all(checks.values())
    print("── soul_egress_filter selftest ──")
    for name, passed in checks.items():
        print(f"  [{'✅' if passed else '❌'}] {name}")
    print(f"  redacciones: {len(red.map)} | razones: {sorted(set(red.hits))}")
    print(f"RESULTADO: {'PASS ✅' if ok else 'FAIL ❌'}")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="corre la verificación por efecto (sin red)")
    ap.add_argument("--port", type=int, default=9098)
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())

    # Modo proxy (CANARIO): requiere fastapi/httpx/uvicorn (importados a nivel módulo).
    if FastAPI is None:
        print("ERROR: faltan fastapi/httpx/uvicorn para el modo proxy.", file=sys.stderr)
        sys.exit(2)

    import os as _os
    UPSTREAM = _os.environ.get("SOUL_EGRESS_UPSTREAM", "https://api.anthropic.com")
    app = FastAPI(title="SOUL Egress Filter (canary)")

    @app.get("/_filter_health")
    async def health():
        # Fail-closed (pedido ADA/William 18-jul): un health "ok" con la deny-list vacía
        # es un VERDE FALSO — el proxy corre pero no protege nada. Los launchers usan
        # este endpoint como gate (HTTP 200 exacto) para decidir si rutean por acá; un
        # 503 los hace caer a DIRECTO con el warning existente, en vez de mentir "ok".
        pattern_names = [r for r, _ in SENSITIVE_PATTERNS]
        degraded = []
        if not pattern_names:
            degraded.append("no_patterns_loaded")
        if len(SENSITIVE_LITERALS) == 0:
            degraded.append("no_literals_loaded")
        body = {"service": "soul-egress-filter", "mode": "canary",
                "patterns": pattern_names, "literals": len(SENSITIVE_LITERALS),
                "degraded": degraded}
        if degraded:
            body["status"] = "degraded"
            return JSONResponse(body, status_code=503)
        body["status"] = "ok"
        return body

    @app.post("/v1/messages")
    async def messages(request: Request):
        body = await request.json()
        red = Redactor()
        minimized = walk_redact(body, red)
        # cabeceras de auth pasan tal cual (proxy auth-transparente)
        # Forzar identidad (sin gzip): httpx agrega accept-encoding:gzip por defecto;
        # si Anthropic comprime el SSE, el relay/rehidratación sobre bytes comprimidos
        # se corrompe (UnicodeDecodeError byte 0x8b). Override explícito a identity.
        fwd_headers = {k: v for k, v in request.headers.items()
                       if k.lower() not in ("host", "content-length", "accept-encoding")}
        fwd_headers["accept-encoding"] = "identity"
        stream = bool(body.get("stream"))
        client = httpx.AsyncClient(timeout=600)
        has_redactions = bool(red.map)
        if stream:
            # httpx expone status/headers apenas llegan, SIN leer el body, vía
            # client.send(..., stream=True) — una sola conexión real. Con eso
            # decidimos ANTES de responder: un 401/4xx/5xx real del upstream se
            # reenvía como error normal (status real, body real), en vez de
            # llegarle al agente disfrazado de 200 solo por ser streaming
            # (bug hallado por ADA, auditoría 18-jul: "401 se convierte en 200").
            req = client.build_request("POST", f"{UPSTREAM}/v1/messages",
                                        json=minimized, headers=fwd_headers)
            upstream_resp = await client.send(req, stream=True)
            if upstream_resp.status_code >= 400:
                err_body = await upstream_resp.aread()
                await upstream_resp.aclose()
                await client.aclose()
                try:
                    err_json = json.loads(err_body) if err_body else {}
                except json.JSONDecodeError:
                    err_json = {"raw": err_body.decode("utf-8", "replace")}
                return JSONResponse(err_json, status_code=upstream_resp.status_code,
                                    headers=_forwardable_response_headers(upstream_resp.headers))

            async def gen():
                # Caso común (sin redacciones): PASSTHROUGH crudo byte-a-byte — no toca
                # el framing SSE, no cuelga. Solo si hubo redacciones se re-hidrata en
                # vuelo (decoder UTF-8 incremental + retención de token parcial).
                import codecs
                dec = codecs.getincrementaldecoder("utf-8")()
                pending = ""
                try:
                    async for raw in upstream_resp.aiter_raw():
                        if not has_redactions:
                            yield raw
                            continue
                        # Frame-aware: emitir SOLO eventos SSE completos (separados
                        # por \n\n) para NO partir un frame (evita 'Could not parse').
                        pending += dec.decode(raw)
                        while "\n\n" in pending:
                            event, pending = pending.split("\n\n", 1)
                            yield (red.rehydrate(event) + "\n\n").encode("utf-8")
                    if has_redactions:
                        tail = dec.decode(b"", final=True)
                        if tail:
                            pending += tail
                        if pending:
                            yield red.rehydrate(pending).encode("utf-8")
                finally:
                    await upstream_resp.aclose()
                    await client.aclose()
            return StreamingResponse(gen(), media_type="text/event-stream",
                                      status_code=upstream_resp.status_code,
                                      headers=_forwardable_response_headers(upstream_resp.headers))
        r = await client.post(f"{UPSTREAM}/v1/messages", json=minimized, headers=fwd_headers)
        await client.aclose()
        data = r.json()
        # re-hidratar el texto de la respuesta no-stream
        for block in data.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                block["text"] = red.rehydrate(block.get("text", ""))
        return JSONResponse(data, status_code=r.status_code,
                             headers=_forwardable_response_headers(r.headers))

    # Catch-all (registrado ÚLTIMO → no shadowea /v1/messages) para las rutas
    # auxiliares de Claude Code (count_tokens, /v1/models...). Sin esto, claude
    # CUELGA al primer endpoint no manejado (404). Cuerpos JSON pasan por el
    # mismo Redactor que /v1/messages: count_tokens lleva el prompt completo
    # (ADA lo confirmó saliendo en claro — auditoría 18-jul), no es de fiar
    # dejarlo "transparente" solo porque no tiene handler dedicado.
    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def passthrough(full_path: str, request: Request):
        raw_body = await request.body()
        ctype = request.headers.get("content-type", "")
        red = None
        send_body: bytes = raw_body
        if raw_body and "application/json" in ctype.lower():
            try:
                parsed = json.loads(raw_body)
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                red = Redactor()
                send_body = json.dumps(walk_redact(parsed, red)).encode("utf-8")
        fwd = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "content-length")}
        async with httpx.AsyncClient(timeout=600) as c:
            up = await c.request(request.method, f"{UPSTREAM}/{full_path}",
                                 content=send_body, headers=fwd,
                                 params=dict(request.query_params))
        resp_body = up.content
        if red is not None and red.map:
            up_ctype = up.headers.get("content-type", "")
            if "application/json" in up_ctype.lower():
                resp_body = red.rehydrate(up.text).encode("utf-8")
        from fastapi import Response as _Resp
        return _Resp(content=resp_body, status_code=up.status_code,
                     headers={k: v for k, v in up.headers.items()
                              if k.lower() not in ("content-encoding", "transfer-encoding", "content-length")})

    loaded = load_sensitive_config()
    print(f"SOUL Egress Filter (CANARY) en http://localhost:{args.port}/v1  — cableado a NADA")
    print(f"  literales TIER-0 cargados del config real: {loaded}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
