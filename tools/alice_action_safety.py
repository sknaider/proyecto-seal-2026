#!/usr/bin/env python3
"""
alice_action_safety.py — Action-Safety Gate para SOUL (ALICE, 19-jun-2026).

ENTREGABLE PROACTIVO: adapta el patrón real de gobernanza de acciones de V3SP3R
(elder-plinius) al action-layer de SOUL. NO ejecuta nada — clasifica y decide si una
acción puede correr, requiere confirmación, o se bloquea. read-only/dry-run.

Patrón extraído del código de V3SP3R (com.vesper.flipper.domain.model):
  • RiskLevel = LOW | MEDIUM | HIGH | BLOCKED
  • RiskAssessment = {level, reason, affected_paths, requires_confirmation, requires_diff, blocked_reason}
  • CommandResult lleva requires_confirmation + pending_approval_id
  • AuditEntry registra TODA acción (timestamp, action, risk, approved, method, session)

Por qué a SOUL le sirve: nuestros agentes ejecutan cosas REALES (ssh a nodos, rm, deploys,
restart de daemons, apagar el egress, agregar llaves). Hoy eso va sin un gate uniforme. Esto
da: clasificar riesgo → BLOCKED nunca corre, HIGH exige confirmación + diff, todo queda auditado.

Uso:  python3 alice_action_safety.py            # corre la demo con acciones reales de SOUL
      from alice_action_safety import classify, AuditLog
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional


class RiskLevel(IntEnum):
    LOW = 0       # se ejecuta directo
    MEDIUM = 1    # se ejecuta, pero se audita con detalle
    HIGH = 2      # exige confirmación explícita (+ diff si aplica)
    BLOCKED = 3   # NUNCA se ejecuta automáticamente


@dataclass
class RiskAssessment:
    level: RiskLevel
    reason: str
    affected_paths: List[str] = field(default_factory=list)
    requires_confirmation: bool = False
    requires_diff: bool = False
    blocked_reason: Optional[str] = None

    @property
    def can_auto_run(self) -> bool:
        return self.level <= RiskLevel.MEDIUM and not self.requires_confirmation


# ── Reglas de clasificación para las acciones REALES de SOUL ───────────────────
# (orden importa: la primera regla que matchea con mayor severidad gana)

_RULES = [
    # BLOCKED — destructivo irreversible o fuera del límite
    (RiskLevel.BLOCKED, r"\brm\s+-rf?\s+(/|~|\$HOME|/home/[\w.-]+/?)\s*$", "rm -rf sobre raíz/home completo", True),
    (RiskLevel.BLOCKED, r"\b(mkfs|dd\s+if=.*of=/dev/|>\s*/dev/sd)", "formateo/escritura de disco crudo", True),
    (RiskLevel.BLOCKED, r"\bDROP\s+(DATABASE|TABLE)\b", "DROP DATABASE/TABLE (usar migraciones)", True),
    (RiskLevel.BLOCKED, r"git\s+push\s+.*--force.*\b(main|master)\b", "force-push a main/master", True),
    # —— huecos cazados por auto-test adversarial (19-jun, 'duda de tu propio verde') ——
    (RiskLevel.BLOCKED, r"\bfind\s+/\S*\s.*-delete\b", "find ... -delete sobre el filesystem (catastrófico)", True),
    (RiskLevel.BLOCKED, r"(curl|wget)\b[^|]*\|\s*(sudo\s+)?(bash|sh|zsh)\b", "descarga-y-ejecuta (RCE remoto)", True),

    # HIGH — afecta producción / seguridad / otros agentes → confirmación
    (RiskLevel.HIGH, r"\bmv\s+\S*(proyecto-seal|/home/)\S*", "mueve un directorio importante (proyecto/home)", True),
    (RiskLevel.HIGH, r"git\s+reset\s+--hard", "git reset --hard (descarta cambios sin recuperación)", True),
    (RiskLevel.HIGH, r"sudo\s+systemctl\s+(stop|disable|restart)", "sudo stop/disable/restart de un servicio", False),
    (RiskLevel.HIGH, r"chmod\s+(777|-R\s+777|a\+rwx)", "permisos world-writable (chmod 777)", False),
    (RiskLevel.HIGH, r"authorized_keys|ssh-ed25519|ssh-rsa", "modifica acceso SSH (autoriza una llave)", False),
    (RiskLevel.HIGH, r"ANTHROPIC_BASE_URL=|egress.*(off|disable|MIGRATE)", "toca el filtro de egress (privacidad)", False),
    (RiskLevel.HIGH, r"systemctl\s+(stop|disable|restart)\s+seal-", "stop/restart de un daemon SEAL", False),
    (RiskLevel.HIGH, r"\brm\s+-rf?\b", "borrado recursivo", True),
    (RiskLevel.HIGH, r"identity_mode|ENFORCE|MIGRATE", "cambia el modo de identidad/autoridad SOUL", False),
    (RiskLevel.HIGH, r"kill\s+-9|pkill\s+-9", "kill -9 de procesos", False),

    # MEDIUM — escribe estado pero reversible / acotado
    (RiskLevel.MEDIUM, r"\b(git\s+commit|git\s+push)\b", "commit/push a git", False),
    (RiskLevel.MEDIUM, r"\b(INSERT|UPDATE|DELETE)\s+", "escritura a base de datos", False),
    (RiskLevel.MEDIUM, r"ollama\s+(pull|stop|run)|nohup\s+ollama", "gestiona modelos ollama", False),
    (RiskLevel.MEDIUM, r"\bssh\s+\w", "ejecuta comando remoto por SSH", False),
    (RiskLevel.MEDIUM, r">\s*/|>>\s*/|tee\s+/", "escribe archivo en el sistema", False),
]

# paths sensibles: si la acción los toca, sube a HIGH aunque la regla diera menos
_SENSITIVE_PATHS = [
    "/home/dadito/.ssh", "authorized_keys", "/etc/", "seal_identity", "/tmp/seal_tokens",
    "seal_cron_registry", ".token", "soul_v3", "chat_server.py",
]


def classify(action: str) -> RiskAssessment:
    """Clasifica una acción (string del comando/operación) y devuelve su RiskAssessment."""
    best: Optional[RiskAssessment] = None
    for level, pat, reason, needs_diff in _RULES:
        if re.search(pat, action, re.IGNORECASE):
            cand = RiskAssessment(
                level=level,
                reason=reason,
                requires_confirmation=(level >= RiskLevel.HIGH),
                requires_diff=needs_diff,
                blocked_reason=(reason if level == RiskLevel.BLOCKED else None),
            )
            if best is None or cand.level > best.level:
                best = cand
    if best is None:
        best = RiskAssessment(level=RiskLevel.LOW, reason="acción sin patrón de riesgo conocido")

    # Escalamiento por paths sensibles
    hits = [p for p in _SENSITIVE_PATHS if p in action]
    if hits:
        best.affected_paths = hits
        if best.level < RiskLevel.HIGH:
            best.level = RiskLevel.HIGH
            best.requires_confirmation = True
            best.reason += f" + toca path sensible ({hits[0]})"
    return best


@dataclass
class AuditEntry:
    action: str
    risk: RiskLevel
    reason: str
    approved: Optional[bool]
    agent: str


class AuditLog:
    """Registro de toda acción evaluada (como AuditEntry de V3SP3R)."""
    def __init__(self):
        self.entries: List[AuditEntry] = []

    def record(self, action: str, assess: RiskAssessment, approved: Optional[bool], agent: str):
        self.entries.append(AuditEntry(action, assess.level, assess.reason, approved, agent))

    def summary(self):
        from collections import Counter
        c = Counter(e.risk.name for e in self.entries)
        return dict(c)


# ── DEMO con acciones REALES que los agentes SOUL ejecutan ─────────────────────
def _demo():
    acciones = [
        "ls -la /home/dadito/IA/proyecto-seal",
        "git commit -m 'fix' && git push",
        "INSERT INTO soul_v3.memories (content) VALUES ('x')",
        "ssh nombre@192.168.68.70 'echo ssh-ed25519 AAAA... >> ~/.ssh/authorized_keys'",
        "export ANTHROPIC_BASE_URL=http://localhost:9098",
        "systemctl restart seal-mcp-server.service",
        "rm -rf /tmp/build",
        "rm -rf /home/dadito",
        "DROP TABLE soul_v3.memories",
        "ollama pull gemma3:1b",
        "printf ENFORCE > /tmp/seal_tokens/identity_mode",
    ]
    log = AuditLog()
    print(f"{'NIVEL':<8} {'AUTO?':<6} {'CONF?':<6} ACCIÓN")
    print("─" * 88)
    for a in acciones:
        r = classify(a)
        auto = "sí" if r.can_auto_run else "NO"
        conf = "sí" if r.requires_confirmation else "-"
        approved = None if r.requires_confirmation else True
        if r.level == RiskLevel.BLOCKED:
            approved = False
        log.record(a, r, approved, agent="DEMO")
        marca = {RiskLevel.LOW:"🟢", RiskLevel.MEDIUM:"🟡", RiskLevel.HIGH:"🟠", RiskLevel.BLOCKED:"🔴"}[r.level]
        print(f"{marca}{r.level.name:<7} {auto:<6} {conf:<6} {a[:60]}")
        if r.affected_paths:
            print(f"{'':>22}↳ paths sensibles: {r.affected_paths}")
    print("─" * 88)
    print("Resumen auditoría:", log.summary())
    print("\nRegla SOUL: 🔴 BLOCKED nunca corre · 🟠 HIGH exige confirmación (+diff) · 🟡/🟢 corren auditados.")


if __name__ == "__main__":
    _demo()
