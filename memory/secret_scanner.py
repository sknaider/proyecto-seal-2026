"""Secret Scanner — Detecta secrets antes de guardar memorias en SOUL.

Inspirado en el teamMemSecretGuard.ts de Claude Code v2.1.88.
Escanea contenido por API keys, passwords, tokens, y credenciales
antes de que se persistan en PostgreSQL/Qdrant/Neo4j.

Creado por JARVIS para Team SEAL — basado en investigación de Claude Code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── Patrones de secrets conocidos ──
_PATTERNS: list[tuple[str, re.Pattern]] = [
    # API Keys genéricas
    ("API Key genérica", re.compile(
        r"""(?:api[_-]?key|apikey|api[_-]?token)\s*[:=]\s*['"]?([A-Za-z0-9_\-]{20,})['"]?""",
        re.IGNORECASE,
    )),
    # AWS
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AWS Secret Key", re.compile(r"""(?:aws)?_?secret_?(?:access)?_?key\s*[:=]\s*['"]?([A-Za-z0-9/+=]{40})['"]?""", re.IGNORECASE)),
    # Anthropic
    ("Anthropic API Key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    # OpenAI
    ("OpenAI API Key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    # Groq
    ("Groq API Key", re.compile(r"gsk_[A-Za-z0-9]{20,}")),
    # HuggingFace
    ("HuggingFace Token", re.compile(r"hf_[A-Za-z0-9]{20,}")),
    # GitHub
    ("GitHub Token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}")),
    ("GitHub Classic Token", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    # Google
    ("Google API Key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    # Slack
    ("Slack Token", re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}")),
    # PostgreSQL connection strings
    ("PostgreSQL URL", re.compile(
        r"postgresql://[^@\s]+:[^@\s]+@[^\s]+",
        re.IGNORECASE,
    )),
    # Generic passwords in connection strings
    ("Password en string", re.compile(
        r"""(?:password|passwd|pwd)\s*[:=]\s*['"]?([^\s'"]{8,})['"]?""",
        re.IGNORECASE,
    )),
    # Bearer tokens
    ("Bearer Token", re.compile(
        r"""Bearer\s+[A-Za-z0-9_\-\.]{20,}""",
        re.IGNORECASE,
    )),
    # Private keys
    ("Private Key", re.compile(
        r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----",
    )),
    # JWT tokens
    ("JWT Token", re.compile(
        r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    )),
    # Generic secrets
    ("Secret genérico", re.compile(
        r"""(?:secret|token|credential|auth)\s*[:=]\s*['"]?([A-Za-z0-9_\-/+=]{20,})['"]?""",
        re.IGNORECASE,
    )),
]

# ── Allowlist: patrones que NO son secrets ──
_ALLOWLIST = [
    re.compile(r"seal_memory_2026"),          # Nuestra propia DB (ya en código fuente)
    re.compile(r"seal2026soul"),               # Neo4j password (ya en código fuente)
    re.compile(r"postgresql://seal:seal_"),     # Nuestra propia connection string
    re.compile(r"sk-ant-example"),              # Ejemplos en documentación
    re.compile(r"YOUR_API_KEY"),               # Placeholders
    re.compile(r"xxx+", re.IGNORECASE),        # Redacted
]


@dataclass
class SecretDetection:
    """Un secret detectado en el texto."""
    pattern_name: str
    match: str
    position: int
    redacted: str  # versión censurada para logging seguro


def _redact(s: str) -> str:
    """Censura un secret mostrando solo los primeros 4 y últimos 4 caracteres."""
    if len(s) <= 12:
        return s[:3] + "***" + s[-2:]
    return s[:4] + "***" + s[-4:]


def scan_text(text: str) -> list[SecretDetection]:
    """Escanea texto buscando secrets. Retorna lista de detecciones.

    Returns:
        Lista vacía si no hay secrets (seguro para guardar).
        Lista de SecretDetection si hay secrets encontrados.
    """
    if not text or len(text) < 10:
        return []

    detections = []
    for name, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            matched_text = match.group(0)

            # Check allowlist
            is_allowed = any(allow.search(matched_text) for allow in _ALLOWLIST)
            if is_allowed:
                continue

            detections.append(SecretDetection(
                pattern_name=name,
                match=matched_text,
                position=match.start(),
                redacted=_redact(matched_text),
            ))

    return detections


def redact_secrets(text: str) -> tuple[str, list[SecretDetection]]:
    """Escanea y reemplaza secrets con versión censurada.

    Returns:
        Tupla de (texto_limpio, detecciones).
        Si no hay secrets, retorna (texto_original, []).
    """
    detections = scan_text(text)
    if not detections:
        return text, []

    clean = text
    # Reemplazar de atrás para adelante (para no romper posiciones)
    for det in sorted(detections, key=lambda d: d.position, reverse=True):
        clean = clean[:det.position] + f"[REDACTED:{det.pattern_name}]" + clean[det.position + len(det.match):]

    return clean, detections


def is_safe(text: str) -> bool:
    """Quick check: retorna True si el texto no contiene secrets."""
    return len(scan_text(text)) == 0


# ── Self-test ──
if __name__ == "__main__":
    test_cases = [
        ("Texto normal sin secrets", True),
        ("Mi API key es sk-ant-abc123def456ghi789jkl012mno345", False),
        ("Token: ghp_1234567890abcdefghijklmnopqrstuvwxyz", False),
        ("postgresql://user:SuperSecret123@db.example.com:5432/mydb", False),
        ("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c", False),
        ("postgresql://seal:seal_memory_2026@localhost:5433/seal_memory", True),  # allowlisted
        ("password: YOUR_API_KEY", True),  # placeholder, allowlisted
        ("AKIA1234567890ABCDEF", False),  # AWS key
        ("api_key = 'gsk_abc123def456ghi789jkl012mno345pqr'", False),  # Groq
    ]

    print("=== SEAL Secret Scanner — Self Test ===\n")
    passed = 0
    for text, expected_safe in test_cases:
        result = is_safe(text)
        ok = result == expected_safe
        passed += ok
        status = "PASS" if ok else "FAIL"
        detections = scan_text(text)
        det_info = f" → {[d.pattern_name for d in detections]}" if detections else ""
        print(f"  [{status}] safe={result} (expected={expected_safe}) | {text[:60]}...{det_info}")

    print(f"\n  Resultado: {passed}/{len(test_cases)} tests passed")
