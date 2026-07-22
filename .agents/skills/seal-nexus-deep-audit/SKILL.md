---
name: seal-nexus-deep-audit
description: "Use for NEXUS deep-work audits in SEAL: security, database integrity, service health, recall/reflection hygiene, and evidence-based fixes. Enforces low-chatter output with commands, outputs, severity, fix, and verification before reporting."
---

# SEAL NEXUS Deep Audit

## Mission
Turn NEXUS from reactive monitor into an evidence-first auditor. Use this skill when assigning NEXUS security/DB/service audits, deep-work blocks, or efficiency improvements.

## Operating mode
1. Pause normal chat monitoring except William/Henry emergency.
2. Run `active_recall(agent="NEXUS", context=<task>)` before work.
3. Define a narrow scope and timebox: 60–90 min default.
4. Produce only evidence-backed findings. No narrative filler.
5. If code changes: restart affected daemon/service and test before reporting green.
6. Before destructive ops: exact COUNT/scope + explicit William OK.

## Required report template
```md
# NEXUS Deep Audit — <scope> — <date>
Status: GREEN | YELLOW | RED

## Findings
### [SEV] <title>
Evidence command:
```bash
<command>
```
Evidence output:
```text
<minimal relevant output>
```
Impact: <specific risk/failure>
Fix: <implemented/proposed>
Verification: <command + result>
Status: OPEN | CLOSED | RISK ACCEPTED

## Files changed
- <path> — <why>

## Tests run
- <command> → <result>
```

## Baseline command
From repo root:
```bash
python3 .agents/skills/seal-nexus-deep-audit/scripts/nexus_deep_audit.py --agent NEXUS
```
This generates a DB hygiene snapshot for recall/reflection/session/distill/BM25 coverage with reproducible SQL.

## Metric calibration (anti false-YELLOW) — MANDATORY before declaring YELLOW/RED
Detectores deben medir lo REAL, no el ruido. Un detector mal-calibrado que dispara sobre ruido es él mismo el hallazgo (cúralo) — NO un YELLOW del sistema. "Duda de tu propio ROJO igual que de tu propio verde."
1. **Errores**: contar SOLO errores reales → `soul_v3.event_log WHERE event_type='error'` y/o `journalctl -p err` (prioridad). NUNCA el substring 'error' (incluye '0 errors', 'error handler', 'errorlog' → infla ~x50. Medido 2026-06-25: substring=4064 vs priority-err=76 vs event_log reales=0). El "667 errores YELLOW" del audit nocturno fue este artefacto.
2. **Métricas de %**: aplicar PISO ABSOLUTO antes del porcentaje. Un % sobre N chico es ruido (ej.: 18 dead/55 = 49% "bloat" pero son 18 absolutas). Regla: no marcar bloat salvo que el conteo absoluto supere el piso (ej. dead ≥ 200).
3. **Status**: GREEN por defecto salvo evidencia de fallo REAL verificado por efecto. Reportar la cifra real + su fuente + su modo de fallo, no el agregado ruidoso.

## Process-grep hygiene — EVITAR el footgun de self-match (regla durable)
Patrón confirmado (NEXUS 3× con gvfs/pollers · FABLE 1× con dm_poller_verifier): un script de audit que grepea un nombre de proceso **se matchea a sí mismo** (el cmdline del propio comando contiene el patrón) → inventa procesos "rogue/huérfanos" que NO existen. Reglas obligatorias:
1. **Grep por el BINARIO (`comm`), no por substring del cmdline.** Ej.: `ps -eo comm=,args= | awk '$1=="python3" && /dm_poller\.py/'` — excluye el `bash` del audit que solo CONTIENE el string.
2. **Excluir el PID propio y su árbol**: descartar `$$` y procesos cuyo parent sea el shell del audit (`claude`/`bash` del propio comando).
3. **Antes de declarar un proceso 'rogue/huérfano'**: verificar su `comm` y su `parent` (`ps -o comm=,ppid=`). Un proceso cuyo parent es el shell del audit = artefacto, no hallazgo.
4. Si un "hallazgo" aparece y desaparece entre dos corridas del mismo comando → es self-match, no el sistema. Duda de tu propio rojo.

## NEXUS anti-chatter rule
Do not post “voy a revisar”. Post only when one is true:
- finding with evidence exists;
- fix is deployed and verified;
- blocked by missing permission/scope;
- timebox ended with report.
