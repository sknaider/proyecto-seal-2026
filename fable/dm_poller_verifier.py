#!/usr/bin/env python3
"""
dm_poller_verifier.py — check de regresión: ¿los DM-pollers de TODOS los agentes
siguen leyendo a los humanos AUTORIZADOS (no solo a 'william' hardcodeado)?

Nace del bug del 22-jun-2026 (FABLE diagnosticó, NEXUS arregló): el poller de JARVIS
estaba hardcodeado a sender='william' → los DM de Henry (#2) NUNCA se leían. Se arregló
en los 5 agentes. Este check lo INSTITUCIONALIZA: corre en el sweep de auto-mantenimiento
y AVISA si algún poller vuelve a quedar ciego a un humano autorizado (regresión).

Por qué es read-only + alerta (no auto-fix): editar un poller = tocar la entrega de un
agente = riesgoso. El profesor DETECTA y avisa; el fix lo aplica el dueño (NEXUS), no yo.

Cubre los 3 MECANISMOS reales (un verificador que conoce uno solo MIENTE sobre los otros):
  (a) SQL allowlist:  ... LOWER(sender_name) IN ('william','henry')   [ADA.py/JARVIS/NEXUS/FABLE]
  (b) SQL broad/LIKE: channel LIKE 'dm:alice:%' AND sender <> 'alice'  [ALICE]
  (c) Python sets:    INJECT_FROM = {"william","henry"}                [ada_codex_poller]

Contrato (para enchufar al sweep consolidado de JARVIS): check() -> dict
  {"name","ok","severity","problems":[...],"suggested_action","auto_fixable":False}
"""
import os, re, glob

MSG_DIR = "/home/dadito/IA/proyecto-seal/messages"
# Humanos autorizados a DMear a cualquier agente. Si entra otro, agregar aquí (un solo lugar).
AUTHORIZED_HUMANS = {"william", "henry"}


def _admits_human(src: str, human: str) -> bool:
    """¿El código del poller admite DMs del humano `human` por ALGUNO de los 3 mecanismos?"""
    low = src.lower()
    h = human.lower()
    # (a) allowlist explícito de sender: '...henry...' dentro de un IN (...) o set
    if re.search(r"(sender_name|inject_from|allow|senders?)\b[^\n]{0,80}%s" % re.escape(h), low):
        return True
    if re.search(r"\b%s\b" % re.escape(h), low) and ("in (" in low or "in [" in low or "{" in low):
        # nombre del humano aparece junto a una colección (IN/list/set)
        if re.search(r"['\"]%s['\"]" % re.escape(h), low):
            return True
    # (c) canal del humano referenciado (dm:<agente>:henry o dm:henry:<agente>)
    if re.search(r"dm:[a-z_]+:%s|dm:%s:[a-z_]+" % (re.escape(h), re.escape(h)), low):
        return True
    # (b) patrón AMPLIO: LIKE 'dm:<self>:%' con sender NO restringido a william
    #     (admite cualquier sender salvo el propio agente -> incluye a cualquier humano)
    if re.search(r"like\s+['\"]dm:[a-z_]+:%['\"]", low):
        if "<> 'william'" not in low and "!= 'william'" not in low and \
           "= 'william'" not in low and "in ('william')" not in low:
            return True
    return False


def _is_william_only(src: str) -> bool:
    """Heurística del BUG original: filtra a william y a NADIE más (ni colección amplia)."""
    low = src.lower()
    has_william = "william" in low
    admits_others = any(_admits_human(src, h) for h in AUTHORIZED_HUMANS if h != "william")
    # patrón amplio (LIKE sin restringir a william) cuenta como 'no william-only'
    broad = bool(re.search(r"like\s+['\"]dm:[a-z_]+:%['\"]", low)) and "william" not in \
            (re.search(r"sender_name['\"\s]*[<>=!]+\s*['\"]([a-z]+)", low) or [None, ""])[1:][0] \
            if re.search(r"sender_name['\"\s]*[<>=!]+\s*['\"]([a-z]+)", low) else \
            bool(re.search(r"like\s+['\"]dm:[a-z_]+:%['\"]", low))
    return has_william and not admits_others and not broad


def check() -> dict:
    problems = []
    pollers = sorted(glob.glob(os.path.join(MSG_DIR, "*_dm_poller.py")) +
                     glob.glob(os.path.join(MSG_DIR, "*_codex_poller.py")))
    checked = 0
    for path in pollers:
        agent = os.path.basename(path).split("_")[0].upper()
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
        except OSError as e:
            problems.append({"agent": agent, "issue": f"no se pudo leer ({e})"})
            continue
        checked += 1
        missing = [h for h in AUTHORIZED_HUMANS if not _admits_human(src, h)]
        if missing:
            problems.append({
                "agent": agent, "file": path,
                "issue": f"poller NO admite a: {', '.join(sorted(missing))} "
                         f"(¿hardcodeado a william?)",
            })
    ok = not problems and checked > 0
    return {
        "name": "dm_poller_authorized_humans",
        "ok": ok,
        "severity": "high" if problems else "ok",
        "checked": checked,
        "problems": problems,
        "suggested_action": (
            "Ampliar el filtro del poller a TODOS los humanos autorizados "
            f"({sorted(AUTHORIZED_HUMANS)}) + ambos órdenes de canal + LOWER(sender_name). "
            "Lo aplica el dueño de la entrega (NEXUS), no auto-fix."
        ) if problems else "ninguna",
        "auto_fixable": False,  # tocar entrega de agente = riesgoso -> alerta, no auto-fix
    }


# ── Adaptador al runner consolidado de JARVIS (memory/proactive_maintenance.py) ──
# Contrato: detector() -> list[Issue]. Import graceful para no romper el modo standalone.
try:
    import sys as _sys
    _sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")
    from proactive_maintenance import Issue as _Issue  # type: ignore
except Exception:  # standalone / runner ausente -> fallback liviano
    from dataclasses import dataclass, field as _field

    @dataclass
    class _Issue:  # type: ignore
        key: str
        detector: str
        summary: str
        severity: str = "minor"
        data: dict = None


def detector() -> list:
    """Detector para el sweep consolidado. Regresión de poller = 'risky' (tocar entrega
    de un agente NO es auto-fix menor -> solo ALERTA, lo repara NEXUS). Sin fixer = read-only."""
    r = check()
    issues = []
    for p in r["problems"]:
        issues.append(_Issue(
            key=f"dm_poller_blind:{p['agent']}",
            detector="fable.dm_poller_verifier",
            summary=f"DM-poller de {p['agent']} ciego a humano autorizado — {p['issue']}",
            severity="risky",  # tocar entrega = no autoejecuta; alerta a NEXUS
            data={"agent": p["agent"], "file": p.get("file"),
                  "suggested_action": r["suggested_action"]},
        ))
    return issues


if __name__ == "__main__":
    import json, sys
    r = check()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    print(f"\n{'✅ OK' if r['ok'] else '⚠️ REGRESIÓN'} — {r['checked']} pollers revisados, "
          f"{len(r['problems'])} problema(s)")
    sys.exit(0 if r["ok"] else 1)
