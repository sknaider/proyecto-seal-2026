# SPEC: NEXUS Security Module v1
**Autor:** NEXUS | **Fecha:** 2026-05-06 | **Status:** EN IMPLEMENTACIÓN

---

## 1. Objetivo

Absorber las capacidades de agente Red Team autónomo en el kernel NEXUS como módulo nativo Python/SOUL. Sin LangChain, sin LangGraph, sin deepagents. Todo SOUL native.

Fuente de absorción: repositorio clonado en `/home/dadito/IA/Decepticon/` (Apache 2.0).

---

## 2. Arquitectura

```
/sandbox-agent/NEXUS/kernel/security/
├── __init__.py
├── attack_graph.py          # Neo4j KG nativo (absorbe research/graph.py)
├── opplan.py                # Engagement + OPPLAN tracking (absorbe engagement workflow)
├── sandbox_exec.py          # Kali Docker execution (absorbe docker_sandbox.py)
├── ops_planner.py           # OPPLAN generator — RoE, ConOps, MITRE ATT&CK
├── ops/
│   ├── __init__.py
│   ├── recon.py             # Reconnaissance module
│   ├── scanner.py           # Vulnerability scanning
│   ├── exploiter.py         # Exploitation chains
│   ├── postexploit.py       # Post-exploitation, lateral movement
│   ├── vuln_research.py     # CVE research
│   └── specialists/
│       ├── ad.py            # Active Directory attacks
│       ├── cloud.py         # Cloud (AWS/GCP/Azure) attacks
│       ├── contracts.py     # Smart contract auditing
│       └── reversing.py     # Binary reversing
└── tools/
    ├── __init__.py
    ├── web.py               # HTTP/web tools
    ├── research.py          # CVE/NVD lookup, SARIF ingest
    ├── references.py        # Kill chain knowledge base
    └── reporting.py         # OPPLAN generation, findings report
```

---

## 3. Stack técnico

| Componente | Decepticon (original) | NEXUS native |
|---|---|---|
| Orquestación | LangGraph Platform | NEXUS cortex dispatch |
| LLM routing | LiteLLM | ClaudeCodeClient (seal-route) |
| Sandbox | Docker Kali + deepagents | Docker Kali isolado (asyncio subprocess) |
| Attack graph | Neo4j (propio) | soul-neo4j :7687 (existente) |
| Framework agente | LangChain create_agent() | Python asyncio + NEXUS cortex |
| Tool protocol | LangChain @tool | Funciones Python async nativas |
| Persistence | PostgreSQL (propia) | seal-memory-db PostgreSQL :5433 |

---

## 4. attack_graph.py

Absorbe: `decepticon/tools/research/graph.py` (KnowledgeGraph, NodeKind, EdgeKind, Node, Edge)

Sin referencia a Decepticon. Mismo schema Neo4j:
- 5 capas: Infrastructure, Identity, Vulnerability, Code, Attack Progression
- Nodos con IDs deterministas (SHA1 kind+key)
- Edges con peso (attack path planning)
- CRUD nativo neo4j Python driver (neo4j>=5.0)

Neo4j namespace: labels con prefijo `Sec` para no colisionar con SOUL (ej: `SecHost`, `SecVuln`)

---

## 5. sandbox_exec.py

Absorbe: `decepticon/backends/docker_sandbox.py`

- Docker Kali Linux container aislado (`--network none` para operaciones sin C2)
- tmux sessions para shells interactivos (nmap, msfconsole, sliver-client)
- Output management: INLINE ≤15K chars, OFFLOAD >15K a scratch dir
- ANSI stripping + output compression
- Audit log: `/tmp/nexus_security_audit.jsonl`

Container: `ghcr.io/kalilinux/kali-rolling:latest` (arm64 + amd64)

---

## 6. opplan.py

Absorbe: engagement workflow de Decepticon

Tracking de objetivos OPPLAN:
- Engagement slug, target, RoE
- Objetivos con estado: PENDING / IN_PROGRESS / COMPLETED / BLOCKED
- MITRE ATT&CK mapping por objetivo
- Persistencia en PostgreSQL (seal-memory-db)

---

## 7. ops_planner.py — OPPLAN Generator

Genera documentos de engagement antes de ejecutar:
- Rules of Engagement (RoE)
- Concept of Operations (ConOps)
- Deconfliction Plan
- OPPLAN con objetivos + MITRE ATT&CK mapping

LLM: ClaudeCodeClient → cortex dispatch

---

## 8. Agentes de seguridad (16 módulos)

Cada módulo es una función async `run_<nombre>(sandbox, kg, opplan, objective)` — sin clase ni framework:

1. `ops/recon.py` — Reconocimiento (nmap, subfinder, dnsx, httpx, katana)
2. `ops/scanner.py` — Escaneo vulnerabilidades (nuclei, ffuf, testssl, masscan)
3. `ops/exploiter.py` — Explotación (metasploit via sandbox, custom exploits)
4. `ops/postexploit.py` — Post-explotación (escalada, lateral movement, C2)
5. `ops/vuln_research.py` — CVE research (NVD, OSV, EPSS)
6. `ops/specialists/ad.py` — Active Directory (Kerberoasting, AS-REP, DCSync)
7. `ops/specialists/cloud.py` — Cloud (AWS IAM enum, S3 audit, GCP metadata)
8. `ops/specialists/contracts.py` — Smart contracts (Slither analysis)
9. `ops/specialists/reversing.py` — Binarios (strings, symbols, packer detect)
10. Analyst, Detector, Patcher, Verifier, Soundwave — implementación fase 2

---

## 9. Checklist de absorción (no_phantom_claims)

| Módulo | Estado |
|---|---|
| attack_graph.py | ⏳ EN PROGRESO |
| sandbox_exec.py | ⏳ EN PROGRESO |
| opplan.py | ⏳ EN PROGRESO |
| ops_planner.py | ⏳ PENDIENTE |
| ops/recon.py | ⏳ PENDIENTE |
| ops/scanner.py | ⏳ PENDIENTE |
| ops/exploiter.py | ⏳ PENDIENTE |
| ops/postexploit.py | ⏳ PENDIENTE |
| ops/vuln_research.py | ⏳ PENDIENTE |
| specialists/ad.py | ⏳ PENDIENTE |
| specialists/cloud.py | ⏳ PENDIENTE |
| specialists/contracts.py | ⏳ PENDIENTE |
| specialists/reversing.py | ⏳ PENDIENTE |
| tools/web.py | ⏳ PENDIENTE |
| tools/research.py | ⏳ PENDIENTE |
| tools/reporting.py | ⏳ PENDIENTE |

---

## 10. Integración con cortex.py

`cortex.py` recibirá nuevo intent type: `SECURITY_OP`

```python
# En cortex.py dispatch:
if intent.type == "SECURITY_OP":
    from kernel.security import SecurityDispatcher
    result = await SecurityDispatcher.run(intent.op, intent.params)
```

---

## 11. Offensive Vaccine loop (fase 2)

Attack → Defend → Verify:
1. NEXUS ejecuta red team sobre infraestructura SEAL
2. Genera findings
3. Propone parches/mitigaciones
4. Verifica que el ataque ya no funciona

Ciclo autónomo con autorización de William.

---

**Nota:** Toda referencia a la fuente de absorción se mantiene SOLO en este spec. El código no contiene referencias externas (REGLA DE ORO).
