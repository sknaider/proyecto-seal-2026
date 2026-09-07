# SOUL SDK — Mapa de Estándares y Adaptadores Externos (v1)

**Owner:** JARVIS (arquitectura + diferenciación) · **Implementa:** ADA (capa compatible +
seguridad + pruebas) · **Fecha:** 2026-07-22
**Orden de William:** el SDK debe ser compatible con herramientas externas no-SOUL
("dulce a un bebé" → adopción → indispensables), sin dejar de ser difícil de replicar.

## Principio arquitectónico (regla dura, de ADA)

```
Estándar externo  →  ADAPTADOR (thin)  →  SDK interno  →  [NÚCLEO: identidad soberana,
                     "el dulce afuera"                     bitemporal, procedencia, RLS]
                                                           ← NUNCA se expone crudo
```

- **El adaptador traduce**, no expone el núcleo. Un cliente externo ve una superficie
  estándar; la identidad firmada / bitemporal / políticas quedan INTERNAS.
- **Export ≠ leak (corrección de ADA, owneada):** el usuario DEBE poder exportar SUS datos
  (portabilidad = feature de confianza). Lo que NO se entrega es la **receta de reproducción**
  del sistema. **El foso = dificultad de REPRODUCIR** (identidad firmada + historia bitemporal
  + procedencia + políticas + aprendizaje), NO atrapar los datos del usuario.

## Estándares a hablar (feasibility verificada por efecto)

| # | Estándar | Estado hoy | Adaptador / trabajo |
|---|---|---|---|
| 1 | **OpenAPI 3.1 / REST** | ✅ VIVO (`/openapi.json` 3.1.0, FastAPI) | ninguno — cualquier lenguaje autogenera cliente |
| 2 | **Auth OAuth / API-key** | ✅ existe (`api_keys`, Bearer, TenantAuth) | mapear a DNI-licencia (ver doc DNI-licensing) |
| 3 | **MCP (Model Context Protocol, Anthropic)** | interno existe; falta **MCP server PÚBLICO** | adaptador MCP→SDK: cualquier cliente MCP (Claude Desktop, Cursor, frameworks) enchufa memoria con CERO código. **El lever de adopción más fuerte** (montarse en el estándar del mundo) |
| 4 | **Adaptadores de frameworks de agentes** | parcial (`soul_lite_adapter`, ejemplos LangChain ref) | memory-backend drop-in para **LangChain / LlamaIndex / CrewAI** → SOUL reemplaza su capa de memoria sin que reescriban nada = el "dulce" |
| 5 | **Export / portabilidad de datos** | a construir | endpoint `GET /v1/export` → memoria del tenant en formato ABIERTO (JSON/JSONL). Da confianza (te podés ir) SIN entregar la receta de reproducción |
| 6 | **Client libraries** | a construir | `pip install soul-memory` / npm — wrappers sobre el REST/OpenAPI |

## Qué NO cruza el adaptador (el núcleo interno, moat)

- Firmas Ed25519 / árboles Merkle / `ssai_keys` privadas → nunca salen.
- Las **políticas RLS** y la lógica de aislamiento per-tenant → internas.
- La **cadena de procedencia** y la invalidación bitemporal → el usuario ve SU historia,
  no el mecanismo que la hace difícil de reproducir.
- La identidad soberana firmada se **verifica** de cara afuera, pero la clave privada y el
  mecanismo de assurance quedan internos.

## Diferenciación (por qué otro no lo replica fácil)

1. **Interop sin fricción (el dulce):** hablan MCP/OpenAPI → adopción trivial.
2. **Foso por depth, no por cárcel:** exportás tus datos cuando quieras; reproducir el
   sistema completo (identidad firmada + bitemporal + procedencia + políticas + aprendizaje)
   es lo caro. Confianza + foso a la vez.
3. **Identidad soberana como licencia** (ver `DESIGN_SDK_DNI_LICENSING_TESTNET_V1.md`): la
   licencia es un DNI verificable, no una key copiable.

## Orden de trabajo sugerido (para ADA implementar)

1. **MCP server público** (mayor palanca de adopción) — adaptador MCP→SDK, gates de seguridad.
2. **Export endpoint** (portabilidad = confianza) — formato abierto, tenant-scoped.
3. **Client libs** (pip/npm) sobre OpenAPI.
4. **Adaptadores de frameworks** (LangChain/LlamaIndex primero).
> Todo detrás del gate de gobernanza para PRODUCCIÓN externa (SSAI M2 testnet-scope →
> OK de William + auditoría NEXUS para DNIs de producción reales).

---
**Estado:** mapa de arquitectura por JARVIS, read-only, corrección de export/moat de ADA
incorporada. Listo para que ADA priorice e implemente la capa compatible.
