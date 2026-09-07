# SEAL Architecture Map — Live State
**Generado por JARVIS | 2026-04-24 01:25 Lima**
**Próxima actualización: semanal (ciclo arquitectural JARVIS)**

---

## Servicios Systemd Activos (20 servicios)

| Servicio | Puerto | Responsable | Estado |
|---|---|---|---|
| seal-studio | :3001 | ADA | UI principal Next.js |
| seal-landing | :3030 | ADA | SEAL Memory landing page |
| seal-chat | :8765 | ADA | WebSocket hub (API principal) |
| seal-mcp-sse | :8766 | JARVIS | MCP SSE daemon (v2, 8 mejoras) |
| seal-soul-api | :8767 | ADA | SOUL API — 22 endpoints REST |
| seal-matrix-bridge | :8008/:8069 | ADA | Matrix ↔ web_chat bidireccional |
| seal-security-monitor | — | NEXUS | OWASP LLM01+LLM08, injection detection |
| seal-memory-anomaly-monitor | — | NEXUS | Drift + burst + signature scan cada 5min |
| seal-resurrect-* (x4) | — | DUM | Auto-restart ADA/JARVIS/ALICE/NEXUS |
| seal-bridge-* (x3) | — | Sistema | Bridges Claude ↔ web_chat |
| seal-watcher | — | Sistema | inotify ADA/JARVIS communication |
| soul-awareness | — | ADA | Autonomous awareness daemon |
| dum-chat-agent | :8899 | DUM | Gemma4 E2B Q8_0 via llama-server |
| seal-dashboard | — | Sistema | Streamlit dashboard |
| seal-dum-watchdog | — | DUM | Silencio detector ADA+JARVIS |

## Bases de Datos Soul DB

| DB | Puerto | Uso |
|---|---|---|
| PostgreSQL | :5433 | Memorias, reglas, OCEAN, relaciones, HMAC |
| Neo4j | :7687 | Grafo temporal — valid_at/invalid_at |
| Qdrant | :6333 | Vectores semánticos — hybrid search |

## Seguridad Implementada (NEXUS)

| Gap | Estado | Descripción |
|---|---|---|
| #1 HMAC | CERRADO ✅ | Firma + verificación en store/recall/hybrid_search |
| #2 Qdrant trust | PENDIENTE | trust_level metadata en memories |
| #3 Drift monitor | CERRADO ✅ | cos_sim tracking, alertas HIGH/CRITICAL |
| #4 Anomaly daemon | CERRADO ✅ | seal_memory_anomaly_monitor.py activo |

## Memory System

- **Hybrid Search**: Qdrant (semántico) + BM25 (keyword) + temporal decay — P95 < 300ms
- **Neo4j**: modelo 4-timestamp (valid_at/invalid_at/created_at/updated_at)
- **Hot/Cold split**: PostgreSQL hot tier + cold tier con compresión
- **HMAC signing**: SHA256, importance>=7 verificadas en boot + recall + search
- **Drift tracking**: original_content_hash + embedding_vector guardados en INSERT (importance>=8)

## SDK seal-memory (mem0-compatible)

```python
from seal_memory import MemoryClient  # drop-in replacement de mem0
```
- Implementado: `sdk/seal_memory/mem0_compat.py` (ADA)
- Migration guide: pendiente ALICE
- Endpoints REST: SOUL API :8767 (22 endpoints, auth + rate-limit)

## Pendientes arquitecturales (JARVIS backlog)

1. **Gap #2 NEXUS**: Qdrant trust segregation + trust_level metadata
2. **Mapa vivo auto-update**: este documento debe actualizarse automáticamente (pendiente script)
3. **Ciclo semanal JARVIS**: revisión arquitectural proactiva — definir trigger
4. **JARVIS herramientas propias**: William autorizó 2026-04-24 — definir scope exacto con William
