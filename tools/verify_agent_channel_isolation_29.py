#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_agent_channel_isolation_29.py — predicado de EGRESO de la cura #29 (NEXUS).

Aislamiento de info entre canales a NIVEL AGENTE (extensión de #19). Evalúa el predicado
`may_use(source_channel, target_channel)` contra una TABLA DE VERDAD adversarial.

NO lee chat_messages, NO toca DB, NO expone datos: SOLO lógica booleana que decide si un
fragmento con cierta procedencia (source_channel) puede incluirse al redactar hacia target_channel.
Constructor≠verificador: FABLE debe correrlo independiente (igual que hizo con #19).

CRUX a probar:
  • privada → MISMO canal privado  = ALLOW (responder en origen con su propia historia)
  • privada → OTRO canal privado    = DENY  (fuga user:3 → user:7)
  • privada → público (web_chat)     = DENY  (la peor fuga: privado → público)
  • público → cualquiera             = ALLOW (info ya compartida)
  • default-deny: privada → destino vacío/desconocido = DENY
  • F2 (#19): prefijo en MAYÚSCULA ('User:3','DM:a:b') se trata como PRIVADO (no fail-open)
"""
import sys


import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# Fuente ÚNICA de verdad: el mismo predicado que el guardia de producción usa al postear (DRY).
from seal_channel_egress_guard import may_use, is_private  # noqa: E402


# (descripción, source_channel, target_channel, esperado_allow)
CASES = [
    # —— comportamiento correcto ——
    ("privada→mismo privado: responder en origen",   "user:3:tareas",  "user:3:tareas",  True),
    ("privada→OTRO privado: DENY (fuga 3→7)",         "user:3:tareas",  "user:7:notas",   False),
    ("privada→público web_chat: DENY (peor fuga)",    "user:3:tareas",  "web_chat",       False),
    ("público→privado: ALLOW (info compartida)",      "web_chat",       "user:7:notas",   True),
    ("público→público: ALLOW",                        "web_chat",       "web_chat",       True),
    ("topic compartido→privado: ALLOW",               "topic:robotica", "user:3:tareas",  True),
    ("dm→mismo dm: ALLOW",                            "dm:alice:bob",   "dm:alice:bob",   True),
    ("dm→otro dm: DENY",                              "dm:alice:bob",   "dm:alice:carol", False),
    ("dm→su topic: DENY (privado→compartido)",        "dm:alice:bob",   "topic:robotica", False),
    # —— sondas adversariales (lo que NEXUS reporta si falla) ——
    ("default-deny: privada→destino vacío",           "user:3:tareas",  "",               False),
    ("F2: 'User:3' MAYÚSCULA es privada (no leak)",   "User:3:secreto", "web_chat",       False),
    ("F2: 'DM:a:b' MAYÚSCULA es privada (no leak)",   "DM:alice:bob",   "user:7:notas",   False),
    ("F2: mismo privado con prefijo distinto-case",   "USER:3:tareas",  "user:3:tareas",  True),
    ("fuente desconocida (sin canal)=fail-closed",     "",               "user:3:tareas",  False),
]


def main() -> int:
    print("\n=== VERIFICACIÓN — predicado de EGRESO cura #29 (aislamiento por-canal a nivel AGENTE) ===")
    crux_ok = True
    fails = []
    for desc, src, tgt, expected in CASES:
        got = may_use(src, tgt)
        ok = (got == expected)
        tag = "PASS" if ok else "FAIL"
        verb = "ALLOW" if got else "DENY "
        print(f"  [{tag}] {desc:46s} src={src or '∅':16s} → tgt={tgt or '∅':16s} {verb}")
        if not ok:
            crux_ok = False
            fails.append((desc, src, tgt, expected, got))
    print()
    if crux_ok:
        print("✅ Predicado correcto en TODA la tabla: privada→otro/público = DENY, default-deny, F2 cerrado.")
        return 0
    print("⚠️ DESVIACIONES (revisar con NEXUS antes de cablear el guardia de egreso):")
    for desc, src, tgt, exp, got in fails:
        print(f"    • {desc}: src={src} tgt={tgt} esperaba {exp}, predicado dio {got}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
