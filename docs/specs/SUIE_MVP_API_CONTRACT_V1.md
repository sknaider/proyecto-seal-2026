# SUIE MVP API Contract v1.1

Estado vivo: `CANDIDATE`. Servicio local `127.0.0.1:8791`; no existe endpoint
de promoción ni escritura directa a `soul_v3.memories`.

## Autenticación

Todos los endpoints `/v1/*` requieren `Authorization: Bearer <token>`. El token
local se lee desde `var/soul_ingestion/service.token` (modo `0600`); nunca se
incluye en logs, fixtures o mensajes.

## Ingesta

| Método y ruta | Entrada principal | Perfil |
|---|---|---|
| `POST /v1/ingest/text` | `text`, `owner_agent`, `tenant_id?`, `source_ref?`, `propose_candidates?` | `generic_v1` u otro explícito |
| `POST /v1/ingest/pdf` | `content_base64`, metadatos y scope | `paper_v1`/`generic_v1` |
| `POST /v1/ingest/email` | MIME en `content_base64`, metadatos y scope | `gtl_operational_v1`/`generic_v1` |
| `POST /v1/ingest/chat` | lista ordenada de mensajes y `allowed_channel` | `team_conversation_v1` fijo |
| `POST /v1/ingest/youtube` | ID de 11 caracteres o URL HTTPS exacta permitida | `generic_v1` |

Respuesta común:

```json
{
  "ok": true,
  "document_id": "uuid",
  "state": "processed|quarantined",
  "idempotent_replay": false,
  "derivation_id": "uuid",
  "candidate_ids": [],
  "digest": "texto extractivo",
  "coverage": {},
  "warnings": [],
  "provenance_complete": true
}
```

`propose_candidates` es `false` por defecto. Si es `true`, las propuestas van
solo a `ingestion_memory_candidates`; siguen sin participar en recall.

## Lectura documental

- `GET /v1/documents/{document_id}?tenant_id=<uuid>` devuelve documento,
  segmentos (incluidos `anchor` y `protected_spans`), derivaciones (incluidos
  `digest`/`coverage`) y candidatos visibles bajo RLS.
- `GET /v1/search?tenant_id=<uuid>&q=<texto>&limit=10` busca únicamente el
  corpus documental staging (`limit` 1–50, consulta 1–500 caracteres).
- `GET /health` comprueba DB, login dedicado `svc_soul_ingestion`, rol efectivo
  `pr_ingestion_processor`, seis tablas staging, artifact store y ausencia de
  privilegio `INSERT` sobre `soul_v3.memories`.

## Contrato del motor para evaluación

- Processor: `extractive_v1@1.1.0`.
- Normalización: `unicode_nfc_text_v2`.
- Ranking: frecuencia léxica + posición + heading + spans + perfil.
- Diversidad: MMR léxico, Jaccard de tokens, `lambda=0.65`.
- Cada cambio de algoritmo/pesos/perfil cambia `config_hash_sha256`.
- Toda frase del digest debe resolver a un rango exacto del original.
- Fechas, dinero, cantidades, IDs, AWB, dosis, negaciones, owners y deadlines son spans
  protegidos y se fuerzan al conjunto seleccionado.

Evaluación sin servicio:

```bash
var/soul_ingestion/audit-venv/bin/python -m pytest -q soul_ingestion/tests
```

La suite incluye regresiones de determinismo, offsets, fuentes hostiles,
aislamiento de recall, trust tier consultivo e idempotencia por bytes crudos.

## Errores y límites

- `401`: bearer ausente/incorrecto.
- `413`: fuente excede el límite del adapter.
- `422`: contrato/encoding/base64/URL inválido.
- `502`: adquisición YouTube falló; el detalle queda redactado.
- `503`: dependencia de capacidad no disponible.

Un límite o parser fallido es `failed`, no una promoción ni una cuarentena
ambigua. No hay `ingest_uri` genérico, ejecución de contenido, cookies de
YouTube ni dependencia LLM/cloud en el camino base.
