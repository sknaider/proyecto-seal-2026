# Bitácora de investigación SSAI

Las entradas son append-only. No borrar una conclusión antigua; agregar una corrección fechada.

## 2026-07-16 — Origen de la tesis

### Pregunta de William

William definió la visión: los modelos cambian y funcionan como cerebros; la identidad del agente debe persistir como personalidad, historia, hash, código, DNI y ADN. Cada agente necesita un registro propio difícil de alterar. William será el custodio principal mientras el sistema sea pequeño y la gobernanza se distribuirá cuando llegue a millones de personas.

### Petición

Investigar ampliamente, generar un spec sustentado y conservarlo como base de una futura tesis, aunque pasen meses antes de retomarla.

### Auditoría local

Se revisaron:

- `soul_v3.identity`;
- `soul_v3.boot_identity_checks`;
- `memory/identity_continuity_v2.py`;
- `memory/migrations/022_identity_continuity_v2_phase0.sql`;
- `memory/seal_trees.py`;
- `memory/soul_vision/custody_sign.py`;
- `memory/spec_soul_identity_privacy_cure.md`;
- `agents/JARVIS/spec_identity_continuity_v2_20260521.md`.

Hallazgo principal: SOUL posee continuidad semántica y componentes criptográficos parciales, pero carece de un registro criptográfico integral por agente, una historia autorizada antirretroceso y un binding fuerte entre identidad y runtime.

Snapshot observado:

- 9 filas en `soul_v3.identity`;
- 0 triggers de usuario sobre `soul_v3.identity`;
- 1,755 filas BIV al momento de la consulta.

### Fuentes primarias contrastadas

- W3C DID Core 1.0.
- W3C Verifiable Credentials 2.0.
- RFC 8032, 8785 y 9162.
- The Update Framework.
- Sigstore/Rekor.
- SPIFFE/SVID.
- SLSA 1.2 e in-toto.
- NIST AI RMF 1.0 y GenAI Profile.
- NIST SP 800-57 y SP 800-207.
- NIST Privacy Framework.
- TPM 2.0, PKCS #11 y systemd Credentials.
- PostgreSQL Row Security.
- OWASP Agentic AI threats.

Las citas canónicas están en `references.bib`.

### Decisiones

1. Usar `urn:soul:agent:<uuid>` en v1.
2. Reservar `did:soul` hasta escribir el método y pasar conformance.
3. JCS + SHA-256 + Ed25519 como perfil inicial.
4. Separar claves de génesis, identidad, custodio, runtime, log y recovery.
5. No guardar memoria privada en manifests públicos.
6. Tratar `soul_v3.identity` como proyección, no raíz de verdad.
7. Exigir UID/contenedor por agente antes de afirmar aislamiento fuerte.
8. Migrar por `SHADOW → DUAL_VERIFY → ENFORCE`.
9. Definir explícitamente que SSAI no demuestra conciencia ni alma metafísica.

### Artefactos creados

- `docs/specs/SPEC_SOUL_SOVEREIGN_AGENT_IDENTITY_V1.md`.
- `docs/thesis/ssai/README.md`.
- `docs/thesis/ssai/THESIS_ROADMAP.md`.
- `docs/thesis/ssai/EVIDENCE_MATRIX.md`.
- `docs/thesis/ssai/RESEARCH_LOG.md`.
- `docs/thesis/ssai/references.bib`.
- `docs/thesis/ssai/MANIFEST.sha256`.

### Próximo experimento

Prototipo `SHADOW` de ADA: DNI génesis, manifest JCS, firmas de dos roles, ledger append-only, manipulación controlada, rollback y continuidad cross-model.

### Estado

Especificación y expediente: completos. Implementación experimental: pendiente. Producción: no iniciada.

## 2026-07-16 — Experimento SSAI SHADOW M1

### Alcance ejecutado

Se construyó el primer prototipo reproducible, sin conexión a PostgreSQL y sin
modificar boot, bridge ni daemons:

- canonicalización JCS/I-JSON fail-closed;
- domain separation `SOUL-ID-MANIFEST-V1\\x00`;
- SHA-256 y Ed25519 con claves efímeras inyectadas;
- UUIDv7 y manifiesto génesis estricto;
- dos aprobaciones independientes (`genesis_root` y `agent_identity`);
- ledger JSONL durable con hash-chain, secuencia estricta y `fsync`;
- witness externo atómico para detectar rollback y fork;
- demo ADA SHADOW que persiste solo material público;
- vectores compartidos entre Python y Node.js.

### Corrección normativa

El ejemplo inicial incluía `genesis_manifest_hash` dentro de la propia génesis.
Eso crea una referencia circular. La regla v1 quedó corregida: el campo es
`null` en `sequence=1`; el ledger registra el hash resultante y las versiones
posteriores lo conservan.

### Evidencia ejecutada

```text
python3 -m pytest -q tests/test_ssai_shadow_*.py
86 passed

python3 -m py_compile memory/ssai_shadow/*.py
exit 0

git diff --check -- memory/ssai_shadow tests/test_ssai_shadow_*.py
exit 0
```

Prueba diferencial adicional del serializador binary64 contra
`JSON.stringify`: 100,000 muestras, 0 diferencias. El corpus no es exhaustivo
sobre los 2^64 patrones posibles.

### Límites

- SHADOW no está conectado al boot ni a `soul_v3.identity`.
- El witness es un archivo separado en el mismo host; producción exige un
  testigo independiente y checkpoints firmados.
- No se persistieron claves, por lo que todavía no existe ceremony, recovery,
  rotación o revocación real.
- Node verificó JCS/hash; Go no se ejecutó porque el toolchain no está instalado.
- No se hizo afirmación de conciencia o alma metafísica.

### Resultado

H2 obtiene evidencia E3 para manipulación de manifiesto, firma, ledger y
rollback. H1 (continuidad entre modelos) todavía no fue ensayada.

### Auditoría cruzada y hardening

Dos revisores paralelos encontraron fallos que la primera suite no cubría:
key IDs sin binding a bytes públicos, posible reutilización de una clave física
para dos roles, evolución autoautorizada, hash previo no recalculado, umbral
derivado del motivo autodeclarado, carrera del witness y replay semántico.

Antes del cierre se corrigieron todos esos puntos en M1:

- las claves públicas forman parte del manifiesto firmado y deben ser únicas;
- `create_approval` y `verify_approvals` comparan la clave con ese commitment;
- el hash anterior se recalcula desde el manifiesto anterior;
- controller set, lineage, algoritmos y timestamps conservan continuidad;
- el umbral se deriva del diff constitucional real;
- recovery/rotation/retirement quedan fail-closed por no estar implementados;
- el witness serializa writers con `flock`;
- cada evento tiene digest semántico único y cada línea debe estar en JCS;
- el verificador persistente revalida ledger, witness, manifest, firmas y policy;
- el reporte público se reconstruye desde el ledger y se detecta su alteración.
- integridad y semántica consumen un único snapshot inmutable bajo lock compartido;
- RFC3339 se limita a microsegundos para evitar retrocesos submicrosegundo.
- un witness rezagado falla cerrado y solo avanza después de verificar toda la
  semántica del ledger;
- la pérdida del witness no activa recuperación automática: M1 falla cerrado
  porque un crash legítimo y un rollback con witness borrado son indistinguibles;
- el formato físico exige exactamente JCS seguido de LF; CRLF es rechazado;
- una repetición idempotente con otro `display_name` es rechazada.

La génesis de demo continúa deliberadamente como `TOFU_UNANCHORED`: prueba
autoconsistencia y posesión de claves, no que la raíz pertenezca a William. Un
pin externo correcto eleva `trust_anchor_verified`; uno distinto es rechazado.
