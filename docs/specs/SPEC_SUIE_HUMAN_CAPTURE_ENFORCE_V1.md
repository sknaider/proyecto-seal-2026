# SUIE Human Capture ENFORCE v1

**Owner:** ADA  
**Fecha:** 2026-08-09  
**Estado:** desplegado en SOUL/SEAL

## Objetivo

Convertir texto humano no estructurado en conocimiento durable sin crear otra
arquitectura ni otro agente. El flujo es una extensión nativa del SOUL Universal
Ingestion Engine (SUIE):

```text
William autenticado
  -> gateway server-side de Studio
  -> staging SUIE (raw + derivación + evidencia)
  -> candidato aislado del recall
  -> bandeja y decisión humana
  -> outbox durable (aprobación/revocación)
  -> promoter aislado -> memory_store/memory_gateway canónicos
  -> PostgreSQL/pgvector + memory_graph_outbox
```

## Invariantes

1. El navegador envía solo texto/título o decisión/motivo. Tenant, owner, scope,
   sensibilidad, actor y sesión se derivan en el servidor.
2. Los tokens SUIE son archivos regulares del usuario, modo `0600`; nunca llegan
   al navegador, logs, cookies o localStorage.
3. Secrets se rechazan antes del artifact store y antes de cualquier INSERT;
   en email se escanean cuerpo, raw y cada parte MIME decodificada.
4. Staging no participa en recall. Todo candidato exige revisión humana.
5. Ingesta, revisión y promoción usan tres logins PostgreSQL distintos, todos
   `NOSUPERUSER/NOBYPASSRLS/NOINHERIT`.
6. Processor, reviewer y promoter no pueden asumir el rol del otro. Reviewer y
   promoter no insertan directamente en `memories` ni en eventos/outbox.
7. Aprobación liga candidato, documento, derivación, hashes, contenido, actor,
   sesión y decisión en un SHA-256 durable.
8. La decisión confirma en milisegundos y queda en un outbox transaccional. Un
   daemon separado reclama con lease, reintenta tras reinicio y usa únicamente
   `memory_store` o `memory_gateway(invalidate)`.
9. Completion verifica worker+lease, binding, tenant, candidato y bytes de la
   memoria canónica. Un ID ajeno o un worker obsoleto fallan cerrado.
10. Revocar invalida bitemporalmente; no borra historia. Reaprobar después de
    revocar está prohibido y la unicidad histórica impide duplicados.
11. Markdown es export opcional; PostgreSQL sigue siendo la fuente canónica.

## Superficie

- Studio: pestaña `Conocimiento` (solo William).
- `POST /api/soul/knowledge`: captura humana privada.
- `GET /api/soul/knowledge`: bandeja.
- `POST /api/soul/knowledge/:candidateId/decision`: aprobar/rechazar/revocar.
- `GET /api/soul/knowledge/export`: export Markdown.
- SUIE interno: `/v1/review/*`, accesible solo con capability server-side.

## Gates de cierre

- suite SUIE completa;
- build/TypeScript de Studio;
- sesión anónima rechazada;
- secret sintético deja cero delta en documentos/artefactos;
- detener el promoter, aprobar, reiniciarlo y comprobar que el outbox pendiente
  se promueve exactamente una vez;
- revocar invalida la memoria y un intento posterior de reaprobar devuelve 409;
- canario rechazado produce evento pero cero memoria;
- `memory_graph_outbox` queda `applied`;
- servicio y panel reiniciados y verificados por HTTP.
