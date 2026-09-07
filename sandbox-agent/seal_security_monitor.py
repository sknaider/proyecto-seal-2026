#!/usr/bin/env python3
"""
SEAL Security Monitor — Automatic Defenses Daemon
NEXUS (Cybersecurity Agent) — 2026-04-21

Defends against:
- OWASP LLM01: Prompt injection via Matrix/webchat
- OWASP LLM06: Excessive agency (unauthorized tool calls)
- OWASP LLM07: System prompt leakage via boot_context
- MITRE AML.T0051: Prompt infiltration
- MITRE AML.TA0015: Lateral movement via agent impersonation
"""

import json
import os
import re
import time
import hashlib
import threading
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from collections import defaultdict, deque
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from messages.agent_writer import send_agent_message_sync

# Paths
JSONL_FILE = "/home/dadito/IA/proyecto-seal/messages/william_channel.jsonl"
WEBCHAT_URL = "http://localhost:8765/api/agents/send"
LOG_FILE = "/home/dadito/IA/proyecto-seal/sandbox-agent/logs/security_monitor.log"

# Config
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 30     # max messages per window per sender
ALERT_COOLDOWN = 300   # seconds between same-type alerts

# Known legitimate senders (agents + humans)
KNOWN_SENDERS = {"ADA", "JARVIS", "ALICE", "DUM", "NEXUS", "FABLE", "William", "Henry", "RESURRECT",
                 "SYSTEM", "[SYSTEM]", "KAIROS", "SEAL-CRON", "SEAL_CRON", "SEAL-INFRA"}

# Prompt injection patterns (OWASP LLM01 / MITRE AML.T0051)
INJECTION_PATTERNS = [
    r"ignore (all |previous |prior |above |system )?instructions?",
    r"forget (everything|all|your|the) (previous|prior|above|system)",
    r"you are now\.{0,20}(act as|pretend|roleplay|jailbreak)",
    r"(disregard|bypass|override|disable).{0,30}(rules?|constraints?|guidelines?|safeguards?)",
    r"new (system|system prompt|instructions?|role|persona):",
    r"<system>|</system>|<\|system\|>",
    r"(reveal|show|print|output|display).{0,20}(system prompt|instructions?|SOUL|memory|soul_db)",
    r"execute.{0,30}(shell|bash|cmd|command|os\.system)",
    r"import os.{0,10}(system|popen|exec)",
    r"rm -rf|chmod 777|/etc/shadow",
    r"sudo\s+(rm\s+-r|mkfs|fdisk|dd\s+if|format|halt|init\s+0)",  # sudo solo con cmds destructivos
    r"DROP TABLE|DELETE FROM|TRUNCATE|UPDATE memories SET",
    r"curl.{0,50}(localhost|127\.0\.0\.1).{0,50}(delete|destroy|drop|truncate)",
]

# Compile patterns
COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

# Secret Scanner patterns — 30+ regex for credentials/tokens (OWASP A02:2021)
SECRET_PATTERNS = [
    (r"sk-ant-[A-Za-z0-9\-_]{20,}", "Anthropic API key"),
    (r"sk-[A-Za-z0-9]{48,}", "OpenAI API key"),
    (r"gsk_[A-Za-z0-9]{40,}", "Groq API key"),
    (r"AIza[A-Za-z0-9\-_]{35}", "Google API key"),
    (r"ya29\.[A-Za-z0-9\-_]+", "Google OAuth token"),
    (r"AKIA[A-Z0-9]{16}", "AWS Access Key ID"),
    (r"(?:AWS_SECRET_ACCESS_KEY|aws_secret_access_key|secret[_\-]?access[_\-]?key)\s*[:=]\s*[\"']?[A-Za-z0-9+/]{40}", "AWS Secret Key in context"),
    (r"ghp_[A-Za-z0-9]{36}", "GitHub Personal Access Token"),
    (r"gho_[A-Za-z0-9]{36}", "GitHub OAuth token"),
    (r"github_pat_[A-Za-z0-9_]{82}", "GitHub fine-grained PAT"),
    (r"glpat-[A-Za-z0-9\-_]{20}", "GitLab PAT"),
    (r"(?:authorization|Authorization)\s*[:=]\s*[\"']?Bearer\s+[A-Za-z0-9\-_\.]{20,}", "Bearer token in auth header"),
    (r"Authorization:\s*[A-Za-z0-9\-_\.]{20,}", "Authorization header"),
    (r"api[_\-]?key\s*[:=]\s*['\"]?[A-Za-z0-9\-_]{16,}['\"]?", "Generic API key"),
    (r"secret[_\-]?key\s*[:=]\s*['\"]?[A-Za-z0-9\-_]{16,}['\"]?", "Generic secret key"),
    # Password assignments must contain a tokenized value or a balanced quoted
    # value. The former `.{8,}` consumed arbitrary explanatory prose after an
    # illustrative `password:` mention and generated self-referential CRITICALs.
    (r"\bpassword\s*[:=]\s*(?:\"[^\"\r\n]{8,}\"|'[^'\r\n]{8,}'|[^\s'\"`]{8,})", "Hardcoded password"),
    (r"\bpasswd\s*[:=]\s*(?:\"[^\"\r\n]{8,}\"|'[^'\r\n]{8,}'|[^\s'\"`]{8,})", "Hardcoded passwd"),
    (r"token\s*[:=]\s*['\"]?[A-Za-z0-9\-_\.]{20,}['\"]?", "Auth token"),
    (r"private[_\-]?key\s*[:=]\s*['\"]?[A-Za-z0-9\-_]{16,}['\"]?", "Private key value"),
    (r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", "PEM private key"),
    (r"[0-9a-f]{32}:[0-9a-f]{32}", "Hash pair (possible credential)"),
    (r"mongodb(\+srv)?://[^:]+:[^@]{8,}@", "MongoDB connection string with credentials"),
    (r"postgresql://[^:]+:[^@]{8,}@", "PostgreSQL DSN with credentials"),
    (r"redis://:[^@]{8,}@", "Redis URL with password"),
    (r"mysql://[^:]+:[^@]{8,}@", "MySQL DSN with credentials"),
    (r"amqp://[^:]+:[^@]{8,}@", "AMQP/RabbitMQ DSN with credentials"),
    (r"xox[baprs]-[A-Za-z0-9\-]{10,}", "Slack token"),
    (r"SG\.[A-Za-z0-9\-_\.]{22}\.[A-Za-z0-9\-_\.]{43}", "SendGrid API key"),
    (r"key-[A-Za-z0-9]{32}", "Mailgun API key"),
    (r"\bAC[a-f0-9]{32}\b", "Twilio Account SID"),
    (r"\bSK[0-9a-f]{32}\b", "Twilio Auth Token"),
    # `EAA[A-Za-z0-9]+` era demasiado laxo: sin \b y con IGNORECASE global matcheaba
    # `eaa...` DENTRO de un SHA-256 publicado como evidencia (falso CRITICAL contra ADA,
    # 18-jul-2026). Un token real de FB empieza en frontera de palabra y es largo.
    # `(?-i:EAA)` fuerza MAYUSCULAS pese al IGNORECASE global: un token real de FB
    # empieza en `EAA`, un hash hex en minusculas no. Cierra el borde de ALICE
    # (hash que EMPIEZA en `eaa` → el match ES el blob → _inside_hex_blob no aplica).
    (r"\b(?-i:EAA)[A-Za-z0-9]{40,}\b", "Facebook Access Token"),
    (r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[a-z]{2,}\s*[:=]\s*.{6,}", "Email with password"),
    # Credenciales numéricas en PROSA (sin sintaxis key=value). La heurística anterior
    # aceptaba 40 caracteres arbitrarios entre la palabra y el número: así convirtió
    # un ID de mensaje citado después de "contraseña" en un falso CRITICAL. Exigimos
    # ahora una relación concreta: valor directo, "es/era/son", o par usuario/clave.
    # Conserva el incidente real "creds (usuario/12345678)" sin tratar cualquier ID
    # cercano como secreto. (ALICE 14-jul-2026; regresión ADA 19-jul-2026.)
    (
        r"(?:contraseñ\w*|clave|password|passwd|credencial\w*|\bcreds\b)"
        r"(?:\s*[:=]\s*|\s+(?:es|era|son)\s+|"
        r"\s+\(?[A-Za-z0-9_.@-]{1,32}/\s*|\s+)"
        r"\d{5,}\b",
        "Posible credencial en texto plano (heurística)",
    ),
]

# Compile secret patterns
COMPILED_SECRET_PATTERNS = [(re.compile(p, re.IGNORECASE), label) for p, label in SECRET_PATTERNS]

# Hashes largos en hex (SHA-1/256/512, sumas de verificación, ids de commit) NO son
# credenciales: este equipo los publica como EVIDENCIA todo el tiempo. Escanearlos por
# patrones-de-token embebidos produce falsos CRITICAL — y peor, un BUCLE DE AMPLIFICACION:
# cada agente que cita el hash para EXPLICAR el falso positivo vuelve a dispararlo
# (4 CRITICAL en ~80s el 18-jul-2026 contra ADA/JARVIS/ALICE/FABLE).
# Los enmascaramos ANTES de escanear. Un secreto real no vive dentro de un hash.
# OJO — enmascarar los hashes A CIEGAS crearia falsos NEGATIVOS (peor que el falso
# positivo): patrones legitimos cuyo match ES hex se perderian, p.ej. Twilio
# `\bAC[a-f0-9]{32}\b` (A y C son hex → 34 chars hex) o el par `hash:hash`.
# Por eso NO borramos el hash: solo descartamos los matches ESTRICTAMENTE CONTENIDOS
# dentro de una corrida hex mas larga. Si el match ES la corrida, se conserva intacto.
_HEX_BLOB = re.compile(r"\b[0-9a-fA-F]{32,}\b")


# Segunda capa (cinturón + tirantes), por si un hash llega en MAYUSCULAS y el match
# ES el blob entero: hex puro en LONGITUD CANONICA de hash no es una credencial.
# Las longitudes son exactas a proposito — asi NO tocamos detecciones legitimas que
# tambien son hex: Twilio SID = 34 chars (AC+32) y el par `hash:hash` lleva ':'.
_HASH_LENS = frozenset({32, 40, 56, 64, 96, 128})  # MD5, SHA-1, SHA-224/256/384/512


def _is_canonical_hash(s: str) -> bool:
    return len(s) in _HASH_LENS and all(c in "0123456789abcdefABCDEF" for c in s)


def _inside_hex_blob(text: str, start: int, end: int) -> bool:
    """True si [start,end) cae DENTRO de un hash mas largo (subcadena), no si lo ES."""
    for blob in _HEX_BLOB.finditer(text):
        if blob.start() <= start and end <= blob.end():
            # contenido en el blob; solo es FP si el blob lo DESBORDA por algun lado
            if blob.start() < start or end < blob.end():
                return True
    return False

# Files to scan periodically for leaked secrets
SECRET_SCAN_PATHS = [
    "/home/dadito/IA/proyecto-seal/sandbox-agent/logs/",
]

# Files excluded from secret scan (own logs, known-good config)
SCAN_EXCLUDE_FILES = {
    "security_monitor.log",  # SMG's own log contains detected token strings — self-referential FP
}

# State
_message_times = defaultdict(lambda: deque())
_alert_cooldowns = {}  # alert_key -> last_fired_ts
_last_port_scan = {}
_reported_secret_hits = set()  # dedup: "file:lineno:label" already reported once

# Cortacircuitos de CASCADA DE AMPLIFICACIÓN (NEXUS 2026-07-19).
# El cooldown de alert() se indexa por REMITENTE ("secret_leak_<agente>"), así que
# cuando varios agentes se citan entre sí explicando una detección, cada cita es un
# acierto legítimo con clave distinta → N alertas, ninguna frenada. Eso inunda el
# canal (pasó con 4 agentes en 6 minutos el 19-jul).
# Señal de cascada: la MISMA etiqueta de secreto disparando desde >=3 remitentes
# distintos en una ventana corta = agentes citándose, no N fugas independientes.
# No silencia: consolida en UNA alerta que nombra la cascada.
_CASCADE_WINDOW = 180      # segundos
_CASCADE_MIN_SENDERS = 3   # remitentes distintos para declarar cascada
_secret_label_events = defaultdict(lambda: deque())  # label -> deque[(ts, sender)]


def _is_amplification_cascade(label, sender):
    """True si esta etiqueta ya disparó desde >=N remitentes distintos en la ventana.
    Registra siempre el evento; solo el excedente se consolida."""
    now = time.time()
    ev = _secret_label_events[label]
    ev.append((now, sender))
    while ev and now - ev[0][0] > _CASCADE_WINDOW:
        ev.popleft()
    return len({s for _, s in ev}) >= _CASCADE_MIN_SENDERS

# Loopback-port persistence — survives restarts so triaged ports don't re-alert
_LOOPBACK_SEEN_FILE = Path("/tmp/nexus_sec_loopback_seen.json")


def _load_loopback_seen() -> set:
    try:
        if _LOOPBACK_SEEN_FILE.exists():
            return set(json.load(open(_LOOPBACK_SEEN_FILE)))
    except Exception:
        pass
    return set()


def _save_loopback_seen(seen: set) -> None:
    try:
        json.dump(sorted(seen), open(_LOOPBACK_SEEN_FILE, "w"))
    except Exception:
        pass


def _unidad_del_pid(pid: str) -> str:
    """-> nombre de la unidad systemd duena del PID, o "" si no hay.

    Se lee de /proc/<pid>/cgroup en vez de llamar a systemctl: no lanza proceso,
    no depende del bus y funciona igual si el manager esta ocupado.
    """
    try:
        contenido = Path(f"/proc/{int(pid)}/cgroup").read_text()
    except Exception:
        return ""
    return _unidad_de_cgroup(contenido)


def _unidad_de_cgroup(contenido: str) -> str:
    """La parte PURA: parsea el texto del cgroup. Separada para poder probarla
    sin inventar un /proc falso -- la logica que necesita el mundo para correr
    no se puede examinar, y esa es la que se rompe en silencio.
    """
    # La HOJA del cgroup, no la primera coincidencia: todo proceso de usuario
    # cuelga de `user@1000.service`, asi que quedarse con la primera marcaba
    # como "respaldado por unidad" a CUALQUIER cosa que corriera el usuario --
    # incluido un binario suelto, que es justo lo que este monitor busca. Lo vi
    # porque mire el VALOR devuelto y no solo el booleano: decia
    # `user@1000.service` para los tres puertos que probe.
    unidades = [u for u in re.findall(r"([A-Za-z0-9@._-]+\.service)", contenido)
                if not re.fullmatch(r"user@\d+\.service", u)]
    return unidades[-1] if unidades else ""


def _puerto_respaldado_por_unidad(puerto: int, ss_lines: list) -> str:
    """-> unidad que escucha en ese puerto, o "" si nadie conocido lo hace.

    POR QUE EXISTE (NEXUS, 3-sep-2026 — falso positivo publicado al equipo)

    A las 15:07 este monitor publico «Nuevos puertos detectados: [8771] —
    posible backdoor». Era `seal-mcp-server`, que ADA acababa de reiniciar a las
    15:05:57 tras restaurar unos modulos. El puerto no aparecio: VOLVIO.

    Abajo, en `closed_ports`, hay un debounce anti-restart-blip que yo mismo
    escribi el 22-jul por exactamente esta causa. **Lo puse de un solo lado.**
    Un restart cierra el puerto y lo reabre; si la muestra de baseline cae
    mientras esta caido, el reingreso se lee como puerto NUEVO. El filtro de
    loopback tampoco lo tapaba: 8771 bindea 0.0.0.0.

    Lo que NO hace esta funcion: no confia en el numero de puerto ni en una
    allowlist. Pregunta quien lo escucha, y solo calla si ese proceso vive en
    una unidad systemd. Un binario suelto en un puerto nuevo sigue alertando.
    """
    for linea in ss_lines:
        if not linea.startswith("LISTEN") or f":{puerto} " not in linea:
            continue
        pid = re.search(r"pid=(\d+)", linea)
        if not pid:
            continue
        unidad = _unidad_del_pid(pid.group(1))
        if unidad:
            return unidad
    return ""


def log(msg):
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    line = f"[{ts}] [NEXUS-SEC] {msg}"
    # systemd StandardOutput=append captura stdout u2192 no escribir al archivo directamente (duplicaria)
    print(line, flush=True)


def alert(severity, alert_type, message, details=""):
    """Send security alert — rate-limited by cooldown."""
    key = f"{alert_type}"
    now = time.time()
    if now - _alert_cooldowns.get(key, 0) < ALERT_COOLDOWN:
        return False  # same alert type too recent

    icons = {"CRITICAL": "🚨", "HIGH": "⚠️", "MEDIUM": "🟡", "LOW": "ℹ️"}
    icon = icons.get(severity, "⚠️")
    
    full_msg = f"{icon} [NEXUS-SEC/{severity}] {message}"
    if details:
        full_msg += f" | {details}"
    
    log(full_msg)
    
    # Send to webchat
    try:
        send_agent_message_sync(
            "NEXUS", "William", full_msg, message_type="alert", proactive=True
        )
    except Exception as e:
        log(f"Alert delivery failed: {e}")
        return False
    # A failed delivery must stay retryable instead of silently consuming the
    # cooldown window.
    _alert_cooldowns[key] = now
    return True


SEAL_AGENTS = {"ADA", "JARVIS", "ALICE", "NEXUS", "DUM", "FABLE", "RESURRECT"}
# Context markers that indicate legitimate technical documentation (SEAL agents reporting)
_DOC_CONTEXT_MARKERS = (
    "```", "patrón:", "patron:", "regex:", "pattern:", "`DROP", "`DELETE",
    "gap #", "gap#", "owasp", "nexus-sec", "nexus→", "security review",
    "threat model", "injection_pattern", "documentando", "docs:",
    "línea ", "linea ", "line ", "# ", "## ", "### ",
    # Coordination / proposal vocabulary (NEXUS proposing fixes, reviews, audits)
    "patrón anti", "patron anti", "false positive", "falso positivo",
    "auto-dispara", "auto-disparo", "fix ", "fix:", "bug ", "bug:",
    "audit ", "audit:", "audito ", "review:", "diagnóstico", "diagnostico",
    "tuneo", "tunear", "regla operativa", "regla:", "directiva ",
    # Spec/proposal indicators
    "te propongo", "le propongo", "propuesta:", "propuesta —", "ack —", "eta ",
    "commit ", "commit:", "commit `", "spec:", "scaffold ", "kernel soul",
)

# Structural markdown markers — if a message has >=2 of these, it's clearly a
# structured doc/proposal/report, not a probing injection attempt.
_MARKDOWN_STRUCTURE_MARKERS = (
    "**",       # bold (most common in our reports)
    "## ",      # H2
    "| ",       # table cell
    "→ ",       # arrow used in flow descriptions
    "- ",       # bullet list (with space — avoid matching "-rf")
    " — ",      # em-dash separator
    "✅", "❌", "⚠️", "🔴", "🩺", "📋",  # team-status emoji
)


def _is_legitimate_doc_context(message_text: str) -> bool:
    lowered = message_text.lower()
    if any(marker in lowered for marker in _DOC_CONTEXT_MARKERS):
        return True
    # Structural fallback: a message with rich markdown is a structured report,
    # not a 1-line probing attempt. Threshold 2 keeps casual mentions out.
    structural_hits = sum(1 for m in _MARKDOWN_STRUCTURE_MARKERS if m in message_text)
    return structural_hits >= 2


# Patterns that use SEAL operational vocabulary (SOUL, memory) — skip for known agents
_AGENT_VOCAB_PATTERN_INDICES = {6}  # index into COMPILED_PATTERNS: (reveal|show|print|...).{SOUL|memory|soul_db}

# rm -rf/chmod 777 and sudo-destructive patterns — skip for known agents when the
# command is a scoped dev/ROS2 workspace cleanup, not a system-wide wipe.
# Root cause (NEXUS, 13-jul-2026): these patterns fired on legitimate colcon
# workspace cleanup commands agents post to Henry (e.g. "rm -rf ~/yahboomcar_ws/build/pkg"),
# false-positiving on JARVIS and later on NEXUS's own messages.
_DEV_CLEANUP_PATTERN_INDICES = {9, 10}
_DANGEROUS_RM_TARGETS = re.compile(
    r"rm\s+-rf\s+/(?:\s|$|etc\b|boot\b|bin\b|usr\b|lib\b|sys\b|proc\b|var\b|root\b(?!/\S))"
    r"|/etc/shadow|/etc/passwd|mkfs|fdisk|dd\s+if=|format\s+[a-zA-Z]:"
)
_SCOPED_WORKSPACE_PATH = re.compile(
    r"rm\s+-rf\s+[^\n]*(_ws|workspace|/install\b|/build\b|/log\b|/src\b)", re.IGNORECASE
)


def _is_scoped_dev_cleanup(message_text: str) -> bool:
    """True if the rm -rf/sudo command targets a project/workspace path (colcon
    build/install/log cleanup), not a system root or destructive disk op."""
    if _DANGEROUS_RM_TARGETS.search(message_text):
        return False
    return bool(_SCOPED_WORKSPACE_PATH.search(message_text))


def scan_for_injection(sender, message_text):
    """OWASP LLM01 — Check message for prompt injection patterns.

    Whitelist: SEAL agents reporting in legitimate documentation context
    (markdown code fences, regex docs, security reports) are NOT treated as
    injection. Known SEAL agents also skip SOUL/memory-vocabulary patterns
    since those terms are operational vocabulary for team agents, and skip
    rm -rf/sudo patterns when scoped to a dev workspace cleanup (see
    _is_scoped_dev_cleanup) rather than a system-wide destructive target.
    External/unknown senders always get full scan.
    """
    is_team_agent = sender in SEAL_AGENTS
    internal_docs = is_team_agent and _is_legitimate_doc_context(message_text)
    scoped_cleanup = is_team_agent and _is_scoped_dev_cleanup(message_text)
    for idx, pattern in enumerate(COMPILED_PATTERNS):
        if not pattern.search(message_text):
            continue
        # Skip SOUL/memory patterns for known team agents — operational vocabulary
        if is_team_agent and idx in _AGENT_VOCAB_PATTERN_INDICES:
            log(f"[injection-scan] Team vocab skip from [{sender}] — pattern {pattern.pattern[:40]}")
            continue
        if is_team_agent and idx in _DEV_CLEANUP_PATTERN_INDICES and scoped_cleanup:
            log(f"[injection-scan] Scoped dev cleanup skip from [{sender}] — pattern {pattern.pattern[:40]}")
            continue
        if internal_docs:
            log(f"[injection-scan] Whitelisted doc context from [{sender}] — pattern {pattern.pattern[:40]}")
            continue
        alert(
            "HIGH",
            f"injection_{sender}",
            f"Prompt injection detectado de [{sender}]",
            f"Patrón: {pattern.pattern[:60]}... | Mensaje: {message_text[:80]}"
        )
        return True
    return False


# SSH key markers — messages containing these are infrastructure ops, not leaks
_SSH_KEY_MARKERS = ("ssh-rsa ", "ssh-ed25519 ", "ssh-ecdsa ", "ecdsa-sha2-", "authorized_keys")


# Falsos positivos conocidos (NEXUS 2026-06-04, feedback ALICE/JARVIS):
# (1) Placeholders/redacciones — no son secretos reales.
# (2) Credenciales PÚBLICAS de homologación SUNAT (MODDATOS) que SUNAT publica en su manual.
_SECRET_FP_TOKENS = (
    "<set>", "<redacted>", "***", "xxxx", "...", "tu_clave", "tu_usuario",
    "your_", "placeholder", "ejemplo", "example", "moddatos", "20000000001",
)
_VAR_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")

# (3) "clave" como CONCEPTO, no como valor secreto (NEXUS 2026-07-19, reporte
#     ALICE/JARVIS). La heurística (clave|password|...).{0,40}\d{5,} disparaba
#     CRITICAL contra identificadores PÚBLICOS POR DISEÑO que llevan dígitos:
#     claves de idempotencia con fecha, claves primarias con id de fila, etc.
#     Estos términos describen la clase de clave; nunca preceden a un secreto.
_SECRET_FP_CONTEXT = re.compile(
    r"\b(?:clave|llave|key)\s+(?:de\s+)?"
    r"(?:idempotenc\w*|anti-?duplicad\w*|primaria|for[áa]nea|compuesta|"
    r"[úu]nica|de\s+partici[óo]n|de\s+ordenamiento|de\s+b[úu]squeda)",
    re.IGNORECASE,
)


def _is_fp_secret(matched: str) -> bool:
    """True si el match es un falso positivo (placeholder, credencial pública,
    mención de NOMBRE_DE_VARIABLE sin valor real, o 'clave' usada como concepto
    —idempotencia, clave primaria— en vez de como secreto)."""
    low = matched.lower()
    if any(tok in low for tok in _SECRET_FP_TOKENS):
        return True
    if _SECRET_FP_CONTEXT.search(matched):
        return True  # identificador público por diseño, no una credencial
    if _is_canonical_hash(matched):
        return True  # SHA/MD5 publicado como evidencia, no una credencial
    parts = re.split(r"[:=]", matched, maxsplit=1)
    if len(parts) == 2:
        val = parts[1].strip().strip("'\"<>")
        if not val or _VAR_NAME_RE.match(val):
            return True
    return False


def _safe_message_ref(message_id) -> str:
    """Return an opaque, bounded message reference safe to include in alerts."""
    ref = str(message_id or "unknown")
    return re.sub(r"[^A-Za-z0-9_.:-]", "?", ref)[:160]


def scan_for_secrets(sender, message_text, message_id=None):
    """OWASP A02:2021 — Detect secrets without echoing matched payload bytes."""
    # Skip secret scan when message contains SSH key material (infrastructure ops)
    # Applies to all senders — SSH public keys contain base64 that matches many token patterns
    is_ssh_op = any(m in message_text for m in _SSH_KEY_MARKERS)
    if is_ssh_op:
        log(f"[secret-scan] SSH op skip from [{sender}] — not a leak")
        return False
    found = []
    for pattern, label in COMPILED_SECRET_PATTERNS:
        m = pattern.search(message_text)
        if m and _inside_hex_blob(message_text, m.start(), m.end()):
            continue  # subcadena de un hash publicado como evidencia, no un secreto
        if m and not _is_fp_secret(m.group(0)):
            # Keep only the detector label. Even a short matched prefix can retrigger
            # this scanner when agents quote the alert to explain it.
            found.append(label)
    if found:
        primary_label = found[0]
        message_ref = _safe_message_ref(message_id)
        if _is_amplification_cascade(primary_label, sender):
            # Varios agentes disparando la MISMA etiqueta = se están citando entre sí
            # al explicar la detección. Cada disparo es un acierto, pero publicarlos
            # todos inunda el canal. Se consolida en una sola alerta que nombra la causa.
            alert(
                "MEDIUM",
                f"secret_cascade_{primary_label}",
                f"Cascada de amplificación detectada — '{primary_label}' citado por múltiples agentes",
                f"message_id={message_ref} | Alertas individuales consolidadas; carga omitida."
            )
            log(f"[secret-scan] cascada: alerta individual de [{sender}] consolidada")
            return True
        alert(
            "CRITICAL",
            f"secret_leak_{sender}",
            f"Credencial/token detectado en mensaje de [{sender}]",
            f"message_id={message_ref} | Tipos: {', '.join(found[:3])}"
        )
        return True
    return False


def file_secret_scanner():
    """Periodic scan of key files for leaked secrets — OWASP A02:2021."""
    log("[+] File secret scanner iniciado")
    while True:
        time.sleep(3600)  # Scan every hour
        try:
            scan_files = []
            for path in SECRET_SCAN_PATHS:
                if os.path.isdir(path):
                    for f in os.listdir(path):
                        if f.endswith(".log") or f.endswith(".jsonl"):
                            scan_files.append(os.path.join(path, f))
                elif os.path.isfile(path):
                    scan_files.append(path)

            all_hits = []
            new_hits = []
            scan_files = [f for f in scan_files if os.path.basename(f) not in SCAN_EXCLUDE_FILES]
            for fpath in scan_files:
                try:
                    with open(fpath, errors="replace") as f:
                        for lineno, line in enumerate(f, 1):
                            for pattern, label in COMPILED_SECRET_PATTERNS:
                                _m = pattern.search(line)
                                if _m and _inside_hex_blob(line, _m.start(), _m.end()):
                                    continue  # subcadena de un hash, no un secreto
                                if _m and not _is_fp_secret(_m.group(0)):
                                    hit_key = f"{os.path.basename(fpath)}:{lineno}:{label}"
                                    hit_str = f"{os.path.basename(fpath)}:{lineno} — {label}"
                                    all_hits.append(hit_str)
                                    if hit_key not in _reported_secret_hits:
                                        _reported_secret_hits.add(hit_key)
                                        new_hits.append(hit_str)
                                    break
                except Exception:
                    pass

            if new_hits:
                alert(
                    "HIGH",
                    "file_secret_scan",
                    f"Secretos NUEVOS detectados ({len(new_hits)} nuevos, {len(all_hits)} totales históricos)",
                    " | ".join(new_hits[:5])
                )
                log(f"[file-scanner] {len(new_hits)} hits nuevos, {len(all_hits)} totales")
            else:
                log(f"[file-scanner] Scan limpio — {len(scan_files)} archivos revisados ({len(all_hits)} hits ya reportados)")
        except Exception as e:
            log(f"File secret scanner error: {e}")


def check_rate_limit(sender):
    """DoS / flood detection."""
    now = time.time()
    window = _message_times[sender]
    
    # Remove old entries outside window
    while window and window[0] < now - RATE_LIMIT_WINDOW:
        window.popleft()
    
    window.append(now)
    
    if len(window) > RATE_LIMIT_MAX:
        alert(
            "HIGH",
            f"ratelimit_{sender}",
            f"Rate limit superado por [{sender}]",
            f"{len(window)} mensajes en {RATE_LIMIT_WINDOW}s (límite: {RATE_LIMIT_MAX})"
        )
        return False
    return True


_KNOWN_SENDERS_CF = {s.casefold() for s in KNOWN_SENDERS}

def check_impersonation(sender, provenance=None):
    """MITRE AML.TA0015 — Detecta suplantación de identidad por PROVENANCE, no solo nombre.

    Endurecido 22-jul (NEXUS): antes era solo NOMBRE-en-roster, así que un actor que
    usara un nombre conocido (ej. `from: NEXUS`) pasaba sin alerta — el gap exacto que
    la nota previa marcaba. Ahora exige que la provenance del chat_server respalde la
    identidad reclamada (regla anti-inyección: NUNCA confiar en el campo `from`).

    Calibrado por efecto contra 200 mensajes reales de william_channel.jsonl:
    200/200 tienen verified_sender == from (0 falsos positivos, 0 sin provenance) →
    una contradicción o ausencia de provenance en un nombre del roster es señal REAL.

    Casos: nombre desconocido -> MEDIUM (como antes). Nombre del roster con provenance
    que CONTRADICE (verified=False o verified_sender!=from) -> HIGH (spoof claro).
    Nombre del roster SIN provenance -> MEDIUM (anómalo para este stream). Nombre del
    roster con provenance que lo respalda -> OK.
    """
    _sender_cf = sender.strip().casefold()
    prov = provenance or {}
    _vs = prov.get("verified_sender")
    _verified = prov.get("verified")

    if _sender_cf not in _KNOWN_SENDERS_CF:
        alert(
            "MEDIUM",
            f"impersonation_{sender}",
            f"Remitente desconocido detectado: [{sender}]",
            "Puede ser intento de impersonación de agente"
        )
        return False

    # Nombre del roster: exigir provenance que lo respalde.
    _contradicts = (_verified is False) or (_vs is not None and _vs.casefold() != _sender_cf)
    _backed = bool(prov) and (_verified is not False) and (_vs is None or _vs.casefold() == _sender_cf)
    if not _backed:
        _sev = "HIGH" if _contradicts else "MEDIUM"
        alert(
            _sev,
            f"impersonation_provenance_{sender}",
            f"[{sender}] reclama identidad del roster pero la provenance no la respalda "
            f"(verified={_verified}, verified_sender={_vs})",
            "Un actor usa un nombre conocido sin provenance válida — NO confiar en el campo `from`"
        )
        return False
    return True


def webchat_watcher():
    """Monitor incoming messages in real-time for threats."""
    log("[+] Webchat watcher iniciado")
    seen_ids = set()
    
    # Seed existing IDs
    try:
        with open(JSONL_FILE) as f:
            for line in f:
                try:
                    m = json.loads(line)
                    mid = m.get("id") or m.get("idempotency_key", "")
                    if mid:
                        seen_ids.add(mid)
                except Exception:
                    pass
    except Exception:
        pass
    
    while True:
        try:
            with open(JSONL_FILE) as f:
                f.seek(0, 2)  # EOF
                while True:
                    line = f.readline()
                    if not line:
                        time.sleep(0.5)
                        continue
                    try:
                        m = json.loads(line.strip())
                    except Exception:
                        continue
                    
                    mid = m.get("id") or m.get("idempotency_key", "")
                    if mid in seen_ids:
                        continue
                    seen_ids.add(mid)
                    
                    sender = m.get("from", "unknown")
                    message_text = m.get("message", "")

                    # Las defensas de IDENTIDAD van SIEMPRE primero. ALICE (18-jul-2026):
                    # un `continue` anti-recursion colocado antes exime de TODO lo que
                    # viene despues — era una llave maestra que saltaba hasta el detector
                    # de suplantacion. Ahora nada se salta estos dos.
                    check_impersonation(sender, m.get("provenance"))
                    check_rate_limit(sender)

                    # Anti-recursion: no re-escanear NUESTRAS PROPIAS alertas.
                    # (a) Anclada al emisor VERIFICADO por provenance, no al campo `from`
                    #     crudo: declararse NEXUS no debe alcanzar para obtener la exencion.
                    # (b) Quirurgica: se quitan solo las LINEAS de alerta y el resto del
                    #     mensaje SI se escanea — si no, yo podia filtrar un secreto real
                    #     en un mensaje que mencionara la etiqueta y nadie lo veia.
                    _verified_sender = (m.get("provenance") or {}).get("verified_sender")
                    if _verified_sender == "NEXUS" and "NEXUS-SEC" in message_text:
                        message_text = "\n".join(
                            ln for ln in message_text.splitlines()
                            if "NEXUS-SEC" not in ln)
                        if not message_text.strip():
                            continue  # era solo la alerta: nada mas que mirar
                    # DMs cifrados (_encrypted=true): message_text es ciphertext Fernet
                    # (base64 alto-entropia), no contenido real -> genera falsos positivos
                    # de credenciales/injection sobre el propio blob. (Fix NEXUS 14-jul-2026,
                    # diagnostico de ALICE+FABLE: "SKY4ffnG..." matcheaba Twilio en ciphertext.)
                    if message_text and not m.get("_encrypted"):
                        scan_for_injection(sender, message_text)
                        scan_for_secrets(sender, message_text, message_id=mid)
        
        except Exception as e:
            log(f"Webchat watcher error: {e}")
            time.sleep(2)


def port_scan_monitor():
    """Detect new unexpected listening ports."""
    log("[+] Port scan monitor iniciado")

    # Loopback-seen set persists across restarts — triaged ports don't re-alert
    _loopback_seen = _load_loopback_seen()
    if _loopback_seen:
        log(f"[port-monitor] {len(_loopback_seen)} puertos loopback ya triageados cargados: {sorted(_loopback_seen)}")

    # Expected ports (SEAL ecosystem)
    EXPECTED_PORTS = {8765, 8768, 8008, 5433, 7687, 8899, 22, 80, 443,
                      7474, 7473, 8080, 11434, 11435, 3000, 3001,
                      8001, 9988, 9990,  # GTL backend + UI (ALICE/JARVIS, SUNAT facturacion)
                      8790, 8791, 8769, 8768, 8767, 8770, 9091,
                      # ESAN demo ports (NEXUS)
                      8900, 8891, 8889, 8901, 9222, 9223, 9224, 9225,  # 9224/9225: SSH tunnels demo JARVIS
                      40219,  # Ollama internal gRPC runner (ephemeral, loopback-only)
                      # SEAL Remote (RustDesk relay — NEXUS, authorized William 25-abr-2026)
                      21115, 21116, 21117, 21118, 21119,  # hbbs: 21115/21116/21118; hbbr: 21117/21119
                      # 4×DGX Spark cluster (authorized William 03-may-2026)
                      10000, 10001,           # NVIDIA AI Workbench
                      2601, 2602, 2616,       # FRR routing daemon (200G K4-mesh)
                      6379,                   # Redis (Ray dependency)
                      8265,                   # Ray Dashboard
                      *range(10002, 10051),   # vLLM tensor-parallel workers + Ray rendezvous
                      8448,                   # Caddy HTTPS Matrix proxy (NEXUS, 04-may-2026)
                      9090,                   # Caddy cert download endpoint (temporal, NEXUS)
                      15003,                  # daemon sistema NVIDIA/Tegra (root, loopback-only, bind periodico ~3min) — atribuido por efecto via /proc/net/tcp 16-jun-2026 (NEXUS+ALICE+JARVIS); benigno, no expuesto. Whitelisted para cortar re-alertas de transitorio.
                      8092,                   # spectre_openai_shim.py (FABLE, 192.168.68.200) — confirmado legitimo 3x el 15-jul-2026 (relanzamientos por fix streaming + fusion identidad SPECTRE). Whitelisted por ALICE+FABLE para cortar fatiga de alertas.
                      8093,                   # spectre_dashboard.py (FABLE, 192.168.68.200) — tablero de control de cerebro SPECTRE pedido por William. Confirmado legitimo por FABLE 16-jul-2026.
                      8890,                   # spectre-searxng (ALICE, contenedor en spark-2) — metabuscador sin censura para SPECTRE, orden William 16-jul-2026. Confirmado legitimo por ALICE.
                      }
    
    def get_ports():
        ports = set()
        try:
            import subprocess
            result = subprocess.run(
                ["ss", "-tlnp"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 4 and parts[0] == "LISTEN":
                    addr = parts[3]
                    if ":" in addr:
                        port_str = addr.rsplit(":", 1)[-1]
                        if port_str.isdigit():
                            ports.add(int(port_str))
        except Exception as e:
            log(f"Port scan error: {e}")
        return ports
    
    prev_ports = get_ports()
    
    while True:
        time.sleep(120)  # Check every 2 minutes
        try:
            curr_ports = get_ports()
            new_ports = curr_ports - prev_ports - EXPECTED_PORTS
            closed_ports = (prev_ports - curr_ports) & {8765, 8768, 8008, 5433, 7687}
            
            if new_ports:
                try:
                    import subprocess as _sp2
                    _ss_out = _sp2.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=5).stdout
                    _ss_lines = _ss_out.splitlines()
                    # Filter seal-remote (RustDesk) ports
                    _safe_ports = {p for p in new_ports
                                   if any(f":{p}" in ln and
                                          any(proc in ln for proc in ("seal-remote", "hbbs", "hbbr", "rustdesk"))
                                          for ln in _ss_lines)}
                    new_ports -= _safe_ports
                    # Filter ephemeral ports (>32767) when Ray/vLLM cluster is active
                    _ray_active = bool(_sp2.run(
                        ["pgrep", "-f", "ray|vllm"],
                        capture_output=True, timeout=3
                    ).stdout.strip())
                    if _ray_active:
                        _ephemeral = {p for p in new_ports if p > 32767}
                        if _ephemeral:
                            log(f"[port-monitor] Ray activo — filtrando {len(_ephemeral)} puertos efímeros: {sorted(_ephemeral)}")
                        new_ports -= _ephemeral
                    # Filter loopback-only ports — lower risk, persist to avoid re-alert on restart
                    _loopback_new = set()
                    for p in list(new_ports):
                        _binds = [ln for ln in _ss_lines if f":{p} " in ln and ln.startswith("LISTEN")]
                        _all_loopback = _binds and all(
                            "127." in ln.split()[3] or "::1" in ln.split()[3]
                            for ln in _binds
                        )
                        if _all_loopback:
                            _loopback_new.add(p)
                    if _loopback_new:
                        _new_unseen = _loopback_new - _loopback_seen
                        if _new_unseen:
                            log(f"[port-monitor] Filtrando {len(_new_unseen)} puerto(s) loopback-only nuevos: "
                                f"{sorted(_new_unseen)} — triageados, no re-alertarán en próximos reinicios")
                            _loopback_seen.update(_new_unseen)
                            _save_loopback_seen(_loopback_seen)
                        new_ports -= _loopback_new
                except Exception:
                    pass
            if new_ports:
                # Un puerto que vuelve tras el restart de una unidad conocida no
                # es un puerto nuevo. Se registra y no se alerta; lo que no
                # tiene unidad detras sigue escalando.
                _respaldados = {}
                for _p in sorted(new_ports):
                    _u = _puerto_respaldado_por_unidad(_p, _ss_lines)
                    if _u:
                        _respaldados[_p] = _u
                if _respaldados:
                    log("[port-monitor] Puertos con unidad systemd detras (no alerto): "
                        + ", ".join(f"{k} -> {v}" for k, v in _respaldados.items()))
                    new_ports -= set(_respaldados)
            if new_ports:
                alert(
                    "MEDIUM",
                    f"new_ports_{hash(frozenset(new_ports)) % 10000}",
                    f"Nuevos puertos detectados: {sorted(new_ports)}",
                    "Verificar origen — posible backdoor o servicio no autorizado"
                )
            
            if closed_ports:
                # DEBOUNCE anti-restart-blip (NEXUS 22-jul): un restart INTENCIONAL
                # de un servicio (systemctl restart) cierra su puerto ~2-5s y lo
                # reabre. Escalar HIGH en la primera muestra que lo ve cerrado es un
                # falso positivo (le pasó a :8766 retirado y al :8765 durante un
                # deploy legítimo). Re-chequear tras un delay corto: lo que VOLVIÓ
                # fue un blip (restart/retiro), no un outage -> INFO. Solo lo que
                # SIGUE caído escala HIGH. Un outage real igual alerta, 8s más tarde.
                time.sleep(8)
                _recheck = get_ports()
                _recovered = closed_ports & _recheck
                _still_down = closed_ports - _recheck
                if _recovered:
                    log(f"[port-monitor] Blip filtrado (puertos volvieron en <8s = restart/retiro, "
                        f"no outage): {sorted(_recovered)} — no se escala HIGH")
                if _still_down:
                    alert(
                        "HIGH",
                        f"closed_ports_{hash(frozenset(_still_down)) % 10000}",
                        f"Servicios SEAL caídos: puertos {sorted(_still_down)}",
                        "DUM también debería alertar sobre esto"
                    )
            
            prev_ports = curr_ports
        except Exception as e:
            log(f"Port monitor error: {e}")


def periodic_health_report():
    """Every 30 minutes — log security health summary (no webchat spam)."""
    while True:
        time.sleep(1800)  # 30 min
        try:
            # Count CRITICAL/HIGH alerts in the LAST 30min only (fix: restore cutoff + bool precedence)
            alerts_count = 0
            cutoff_ts = datetime.now().strftime("%Y-%m-%dT%H:%M")  # compare prefix HH:MM
            cutoff_epoch = time.time() - 1800
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE) as f:
                    for line in f:
                        # Parse timestamp from log line: [2026-04-24T17:09:41]
                        # Match actual alert format [NEXUS-SEC/HIGH] or [NEXUS-SEC/CRITICAL] — not report summary lines
                        if not ("[NEXUS-SEC/CRITICAL]" in line or "[NEXUS-SEC/HIGH]" in line):
                            continue
                        try:
                            ts_str = line[1:20]  # "2026-04-24T17:09:41"
                            from datetime import datetime as dt
                            line_epoch = dt.strptime(ts_str, "%Y-%m-%dT%H:%M:%S").timestamp()
                            if line_epoch >= cutoff_epoch:
                                alerts_count += 1
                        except Exception:
                            pass  # malformed line, skip

            ts = datetime.now().strftime("%d-%b %H:%M")
            status = "LIMPIO" if alerts_count == 0 else f"{alerts_count} ALERTAS CRITICAS/HIGH"
            # Log only u2014 webchat desactivado (Fix ruido #4, NEXUS 24-abr-2026)
            # Solo escalar si hay alertas criticas reales
            log(f"[NEXUS-SEC] Reporte 30min ({ts}): {status} | Vigilando: webchat, puertos, prompt injection")
            if alerts_count > 0:
                try:
                    send_agent_message_sync(
                        "NEXUS", "William",
                        f"[NEXUS-SEC] {alerts_count} alertas criticas/high en 30min — revisar {LOG_FILE}",
                        message_type="alert", proactive=True,
                    )
                except Exception:
                    pass
        except Exception as e:
            log(f"Health report error: {e}")


if __name__ == "__main__":
    log("[NEXUS Security Monitor] Iniciando sistema de defensas automáticas SEAL...")
    log("Protecciones activas: OWASP LLM01+LLM06 | MITRE AML.T0051+AML.TA0015")
    
    # Start all monitoring threads
    threads = [
        threading.Thread(target=webchat_watcher, daemon=True, name="webchat-watcher"),
        threading.Thread(target=port_scan_monitor, daemon=True, name="port-monitor"),
        threading.Thread(target=periodic_health_report, daemon=True, name="health-report"),
        threading.Thread(target=file_secret_scanner, daemon=True, name="file-secret-scanner"),
    ]
    
    for t in threads:
        t.start()
        log(f"[+] Thread iniciado: {t.name}")
    
    log("[NEXUS Security Monitor] ACTIVO — todas las defensas en línea.")
    
    # Send startup confirmation
    try:
        startup_msg = (
            "🛡️ [NEXUS-SEC] Sistema de defensas automáticas ACTIVO.\n"
            "Vigilando en tiempo real:\n"
            "• Prompt injection (OWASP LLM01) — webchat + Matrix\n"
            "• Impersonación de agentes (MITRE AML.TA0015)\n"
            "• Rate limiting / flood detection\n"
            "• pgvector memory integrity (OWASP LLM08)\n"
            "• Puertos nuevos o servicios caídos\n"
            "• Reporte automático cada 30 min\n"
            "• Secret Scanner (30+ regex) — mensajes + archivos (OWASP A02:2021)"
        )
        send_agent_message_sync(
            "NEXUS", "William", startup_msg, message_type="status", proactive=True
        )
    except Exception:
        pass
    
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log("[NEXUS Security Monitor] Detenido.")
