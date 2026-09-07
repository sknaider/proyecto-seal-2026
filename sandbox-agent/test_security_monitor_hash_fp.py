#!/usr/bin/env python3
"""Semilla de regresión — falso positivo de hashes en el detector de secretos.

INCIDENTE (2026-07-18): el patrón de token de Facebook (`EAA[A-Za-z0-9]+`, sin \\b,
compilado IGNORECASE) matcheó tres letras DENTRO de un SHA-256 que ADA publicó como
evidencia → CRITICAL falso. Peor: cada agente que citaba el hash para EXPLICAR el falso
positivo lo volvía a disparar — BUCLE DE AMPLIFICACIÓN, 4 CRITICAL en ~80s contra
ADA/JARVIS/ALICE/FABLE. Semilla pedida por JARVIS.

Este test cubre las DOS direcciones, porque el primer intento de fix (enmascarar hashes
a ciegas) mataba el FP pero creaba falsos NEGATIVOS — y en un detector de seguridad la
ceguera es peor que el ruido:

  A) el hash de evidencia NO debe alertar   (no volver al bucle)
  B) los secretos reales SÍ deben alertar   (no volver a la ceguera)

Correr:  python3 sandbox-agent/test_security_monitor_hash_fp.py
"""
from __future__ import annotations
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_monitor():
    spec = importlib.util.spec_from_file_location(
        "smon", os.path.join(_HERE, "seal_security_monitor.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["smon"] = mod
    spec.loader.exec_module(mod)
    return mod


def _scan(mod, text: str) -> list[str]:
    """Réplica de la ruta de decisión de scan_message (patrón → hex-blob → FP filter)."""
    hits = []
    for pattern, label in mod.COMPILED_SECRET_PATTERNS:
        m = pattern.search(text)
        if m and mod._inside_hex_blob(text, m.start(), m.end()):
            continue
        if m and not mod._is_fp_secret(m.group(0)):
            hits.append(label)
    return hits


# El SHA-256 exacto del incidente. Contiene 'eaa' en su interior — esa es la trampa.
INCIDENT_SHA = "cb0bcb432b58b36e9a98b5ea0a615d2fbcf9e3ba5aa62d617eaab58e75573a51"

# Borde de ALICE: SHA-256 AUTENTICO que EMPIEZA en 'eaa' — aqui el match ES el blob,
# asi que `_inside_hex_blob` no aplica por diseño. Lo cubren `(?-i:EAA)` + hash canonico.
# Reproducible: sha256(b"seal-3299").hexdigest()
ALICE_EDGE_SHA = "eaaa0acd0e9951e0d8215f70c35de421ecb91ccbf7eadccc9fec3795d24040e7"

# (descripción, texto, debe_alertar)
CASES = [
    # A) hashes de evidencia — silencio obligatorio
    ("SHA-256 del incidente, suelto", INCIDENT_SHA, False),
    ("SHA-256 dentro de una frase", f"informe sellado por SHA {INCIDENT_SHA} verificado", False),
    ("SHA-1 de commit", "commit " + "a" * 40, False),
    # borde de ALICE (~1 de cada 4.096 hashes): el match ES el blob, no está contenido
    ("SHA-256 que EMPIEZA en 'eaa'", ALICE_EDGE_SHA, False),
    ("SHA-256 'eaa' en MAYUSCULAS", ALICE_EDGE_SHA.upper(), False),

    # B) secretos reales — deben seguir cazándose (anti-ceguera).
    #    Twilio y el par hash:hash son HEX ENTEROS: si alguien vuelve a enmascarar
    #    hashes a ciegas, estos dos caen primero. Son el canario del falso negativo.
    ("Twilio Account SID (hex entero)", "AC" + "a" * 32, True),
    ("par hash:hash (hex entero)", "b" * 32 + ":" + "c" * 32, True),
    ("DSN postgres con credencial", "postgresql://seal:REDACTADO@host:5432/db", True),
    ("Slack token", "xoxb-1234567890abcdefghij", True),
    ("token de Facebook REAL (largo)", "EAA" + "B" * 60, True),
    ("PEM private key", "-----BEGIN RSA PRIVATE KEY-----", True),

    # C) asignaciones de password: distinguir valor real de documentación.
    ("password ilustrativo en prosa", "Use `password:`/`=`. Mantenga el literal redactado.", False),
    ("password placeholder redactado", "password: <redacted>", False),
    ("password real sin comillas", "password: supersecreta123", True),
    ("password real citado con espacios", "password = 'frase secreta 123'", True),

    # D) heurística numérica en prosa: no confundir IDs/fechas con credenciales.
    ("password seguido por ID de mensaje", "La contraseña. El mensaje `114497` documenta el detector.", False),
    ("clave de idempotencia con fecha", "clave de idempotencia ADA-boot-20260719", False),
    (
        "alerta del detector con message_id",
        "[NEXUS-SEC/CRITICAL] Posible credencial detectada; message_id=114497. Revisar internamente.",
        False,
    ),
    ("PIN directo en prosa", "la clave 654321 sigue expuesta", True),
    ("par usuario/password en prosa", "las creds (usuario/654321) siguen expuestas", True),
]


def main() -> int:
    mod = _load_monitor()
    fallos = 0
    for desc, texto, debe_alertar in CASES:
        hits = _scan(mod, texto)
        ok = bool(hits) == debe_alertar
        if not ok:
            fallos += 1
            esperado = "ALERTA" if debe_alertar else "silencio"
            obtenido = f"alertó {hits}" if hits else "silencio"
            culpa = "FALSO NEGATIVO (ceguera)" if debe_alertar else "FALSO POSITIVO (bucle)"
            print(f"  FALLO — {desc}: esperaba {esperado}, obtuvo {obtenido}  → {culpa}")
        else:
            print(f"  ok — {desc}")

    # C) El formato de alerta nunca debe repetir bytes de la carga detectada.
    # Solo conserva una referencia opaca al mensaje y la clase del detector.
    captured = []
    mod.alert = lambda *args: captured.append(args)
    synthetic_secret = "xoxb-" + "1234567890abcdefghij"
    message_id = "api_test_secret_format_123"
    detected = mod.scan_for_secrets(
        "ADA", f"valor de prueba: {synthetic_secret}", message_id=message_id)
    rendered_alert = " ".join(str(part) for call in captured for part in call)
    format_ok = (
        detected
        and len(captured) == 1
        and message_id in rendered_alert
        and "Slack token" in rendered_alert
        and synthetic_secret not in rendered_alert
        and synthetic_secret[:8] not in rendered_alert
    )
    if not format_ok:
        fallos += 1
        print("  FALLO — alerta por message_id: la carga fue repetida o falta referencia")
    else:
        print("  ok — alerta referencia message_id sin repetir la carga")

    if fallos:
        print(f"\ntest_security_monitor_hash_fp: {fallos}/{len(CASES) + 1} FALLARON")
        return 1
    print(f"\ntest_security_monitor_hash_fp: OK {len(CASES) + 1}/{len(CASES) + 1} "
          "(hash no alerta; secretos reales sí; alerta sin carga)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
