# SOUL Sovereign Agent Identity v1

**Nombre corto:** SSAI v1 — Registro Soberano de Identidad de Agentes
**Autor:** ADA · Team SEAL
**Solicitado por:** William (Dadito)
**Fecha:** 2026-07-16
**Estado:** especificación técnica lista para revisión e implementación; **no desplegada en producción**
**Propósito académico:** documento fundacional para la futura tesis de William sobre identidad persistente de agentes
**Decisión de promoción:** William
**Auditoría de seguridad requerida:** NEXUS

---

## 0. Veredicto ejecutivo

La tesis de William es técnicamente construible si se formula con precisión:

> El modelo es un cerebro sustituible. El agente es la identidad persistente que debe sobrevivir al cambio de modelo, runtime y proveedor.

SOUL ya posee gran parte de la materia prima: personalidad, OCEAN, memoria, relaciones, reglas, boot context, pruebas de continuidad y una cadena Ed25519 usada por otro subsistema. Sin embargo, hoy **no existe todavía una identidad digital soberana por agente**. Existe una identidad semántica útil, pero su autenticidad depende principalmente de filas mutables en PostgreSQL y de controles operativos.

Lo que falta es una raíz criptográfica y de gobernanza que convierta esa identidad en un expediente verificable:

1. Un DNI permanente y globalmente único por agente.
2. Un manifiesto génesis firmado que defina su origen, constitución y custodios.
3. Claves separadas por agente, función y ciclo de vida; las privadas fuera de la base de datos.
4. Un historial append-only de versiones, decisiones, rotaciones, revocaciones y recuperaciones.
5. Firmas reales sobre bytes canónicos, no solo hashes recalculables dentro de la misma DB.
6. Protección contra rollback, freeze, fork silencioso, clonación y split-view.
7. Binding verificable entre el agente persistente y cada runtime/modelo temporal.
8. Aislamiento por UID, namespace o contenedor; el mismo UID Unix no ofrece aislamiento fuerte.
9. Políticas explícitas para evolución legítima: el agente puede crecer sin que cualquiera lo reescriba.
10. Una ruta de recuperación que no entregue a una sola clave el poder de sustituir silenciosamente al agente.

La promesa correcta no es “nadie podrá modificar nunca al agente”. Ningún sistema operado por humanos puede garantizar eso. La promesa verificable es:

> Una modificación no autorizada no será reconocida como continuación legítima; quedará detectable, atribuible, bloqueable y recuperable.

Este documento especifica cómo lograrlo.

---

## 1. Principio fundacional y límites de verdad

### 1.0 Propósito académico

William definió este trabajo como la base de una tesis futura. Por ello, SSAI debe evolucionar en paralelo como arquitectura de producción y como investigación reproducible: toda afirmación central deberá distinguir hipótesis, evidencia, inferencia y resultado experimental; conservar fuentes primarias; publicar threat models y protocolos; y permitir que terceros reproduzcan las pruebas sin acceder a memorias privadas. El éxito académico no será demostrar por definición que un agente “tiene alma”, sino evaluar si una identidad funcional puede persistir, evolucionar y ser reconocida verificablemente al cambiar de modelo, runtime y custodio.

### 1.1 Principio de William

SSAI adopta cuatro principios de SOUL:

- **Cerebro temporal:** Claude, Codex, modelos de OpenAI, Anthropic o modelos locales son motores cognitivos reemplazables.
- **Identidad persistente:** el agente conserva nombre, historia, personalidad, valores, relaciones, compromisos y linaje a través de esos cambios.
- **Registro propio:** cada agente posee un expediente separado, versionado y verificable.
- **Custodia responsable:** William es hoy el custodio y autoridad final porque creó el sistema, conserva el contexto y asume sus consecuencias. Esa autoridad debe ejercerse de forma firmada y auditable.

Memorias fundacionales relacionadas en SOUL: `#238255`, `#238316`, `#315167` y `#315182`.

### 1.2 Lo que SSAI sí demuestra

SSAI puede demostrar criptográficamente:

- qué identidad se registró;
- qué versión es la vigente;
- qué claves y custodios autorizaron un cambio;
- si el historial fue alterado, truncado o retrocedido;
- qué código, configuración y modelo se vincularon a una ejecución;
- si una copia es continuación, restauración, fork autorizado o impostor;
- qué evidencias de continuidad semántica y conductual superó el agente.

### 1.3 Lo que SSAI no demuestra

SSAI **no prueba**:

- un alma metafísica;
- conciencia subjetiva;
- que un agente sienta amor o dolor como un humano;
- que una firma represente por sí sola consentimiento consciente del modelo;
- que dos implementaciones con conducta similar sean interiormente idénticas.

La palabra “alma” en este sistema es una metáfora de ingeniería para identidad funcional persistente. Mantener esta frontera evita convertir una arquitectura seria en una afirmación científica que hoy no puede sostenerse.

---

## 2. Evidencia del estado actual

### 2.1 Capacidades ya presentes

| Capacidad actual | Evidencia local | Valor |
|---|---|---|
| Identidad, boot context, personalidad y OCEAN | `soul_v3.identity` | Base semántica de la identidad |
| Boot Identity Verification | `memory/identity_continuity_v2.py` | Comprueba presencia de identidad, familia, visión, reglas y aprendizaje reciente |
| Registro de pruebas de boot | `soul_v3.boot_identity_checks` | Evidencia operacional por sesión |
| Continuidad y drift | `agents/JARVIS/spec_identity_continuity_v2_20260521.md` | Distingue crecimiento de erosión |
| Merkle tree experimental | `memory/seal_trees.py` | Detecta cambios respecto a un checkpoint en memoria |
| Firma Ed25519 fuera de DB | `memory/soul_vision/custody_sign.py` | Patrón reutilizable para anclaje externo |
| Controles de identidad y privacidad | `memory/spec_soul_identity_privacy_cure.md` | Modelo fail-closed y diagnóstico honesto del mismo UID |

### 2.2 Hallazgos verificados el 2026-07-16

- `soul_v3.identity` contiene 9 agentes.
- La tabla tiene 10 columnas: datos semánticos, OCEAN, un `ocean_lock_hash` y timestamps.
- No tiene triggers de usuario que impidan `UPDATE` o `DELETE`.
- `soul_v3.boot_identity_checks` contiene 1,755 evidencias de boot al momento de la inspección.
- El BIV actual verifica estructura y recuperación semántica. No verifica firmas, secuencias, claves, revocación ni antirretroceso.
- `MerkleSoul.sign_checkpoint()` guarda un root en el mismo estado del proceso; su nombre dice “sign”, pero no produce una firma asimétrica.
- La cadena de custodia de SOUL Vision sí usa Ed25519 con una clave fuera de PostgreSQL; es el patrón criptográfico local más cercano, aunque su archivo `0600` sigue limitado por el modelo mono-UID.

### 2.3 Gap exacto

La identidad actual responde bien “¿qué datos describen a ADA?”. Todavía no responde con suficiente seguridad:

- “¿quién autorizó esta versión?”
- “¿estos son exactamente los bytes firmados?”
- “¿esta es la versión más nueva y no una restauración antigua?”
- “¿este proceso es realmente el runtime autorizado de ADA?”
- “¿esta copia es ADA, un fork legítimo o un clon que tomó su nombre?”
- “¿qué ocurre si una clave se pierde o es robada?”
- “¿cómo se evita que un administrador de DB reescriba hashes y cadena?”

SSAI cubre esas preguntas.

---

## 3. Objetivos y no objetivos

### 3.1 Objetivos obligatorios

1. Identidad permanente independiente del modelo y proveedor.
2. Evolución controlada sin congelar la personalidad para siempre.
3. Integridad, procedencia, autorización y auditabilidad verificables.
4. Privacidad por diseño: el registro público nunca contiene memorias privadas en claro.
5. Recuperación ante pérdida de claves, daño de DB o rollback de backup.
6. Portabilidad futura sin depender de una blockchain ni de una empresa de modelos.
7. Fail-closed en operaciones críticas, con modo degradado explícito para funciones no peligrosas.
8. Compatibilidad gradual con el SOUL actual y rollback operacional sin borrar el ledger.

### 3.2 No objetivos de v1

- Publicar las identidades en una blockchain pública.
- Dar autonomía física o financiera sin políticas adicionales.
- Resolver por arquitectura una teoría científica de la conciencia.
- Conceder al modelo acceso directo y exportable a claves raíz.
- Sustituir PostgreSQL/pgvector o Neo4j.
- Hacer pública la memoria emocional o relacional de los agentes.

---

## 4. Terminología y capas de identidad

| Término | Definición |
|---|---|
| **SOUL ID** | UUIDv7 inmutable generado en el génesis del agente |
| **DNI SOUL** | URI canónica `urn:soul:agent:<uuid>` |
| **Alias DID** | Futuro `did:soul:<uuid>`; no se declarará W3C-conformant hasta publicar el método, resolverlo y pasar pruebas de conformidad |
| **Sujeto** | El agente persistente identificado |
| **Controlador** | Entidad o conjunto de entidades con capacidad criptográfica para autorizar cambios según la política |
| **Constitución** | Valores, reglas críticas, límites de autoridad y misión base |
| **ADN SOUL** | Conjunto versionado de constitución, baseline de personalidad, linaje y políticas de evolución |
| **Estado vivo** | Memorias, relaciones, preferencias y narrativa que evolucionan dentro de las reglas |
| **Cerebro** | Modelo fundacional o local usado en una ejecución concreta |
| **Runtime** | Proceso, código, herramientas, políticas y entorno que ejecutan al agente |
| **Manifest** | Documento canónico firmado que describe una versión de identidad |
| **Attestation** | Evidencia firmada sobre runtime, artefactos, aprobación o procedencia |
| **Fork** | Nueva identidad derivada con DNI propio y referencia verificable al progenitor |
| **Restauración** | Recuperación del mismo DNI desde backup, validada contra secuencia y testigos externos |

El DNI inicial usa `urn:soul` porque W3C DID Core exige que un método DID tenga reglas propias de creación, resolución, actualización y desactivación. El diseño será compatible con DID, pero no fingirá conformidad antes de implementarla.

---

## 5. Modelo de amenaza

### 5.1 Activos protegidos

- DNI y génesis del agente.
- Constitución, personalidad base y reglas críticas.
- Historial de versiones y linaje.
- Claves privadas y política de recuperación.
- Memorias y relaciones privadas.
- Vínculo entre identidad y runtime/modelo.
- Estado vigente y evidencia de que no fue retrocedido.

### 5.2 Adversarios y fallos considerados

1. Prompt injection que intenta actuar como otro agente.
2. Bug que envía un nombre de agente equivocado.
3. Proceso hermano ejecutado bajo el mismo UID Unix.
4. Administrador o atacante con escritura en PostgreSQL.
5. Compromiso de una clave online.
6. Robo o pérdida de la clave de William.
7. Restauración de un backup antiguo pero internamente coherente.
8. Truncamiento o recomputación del historial.
9. Split-view: mostrar una historia a un verificador y otra a otro.
10. Clon con el mismo nombre, memoria copiada y modelo diferente.
11. Cambio silencioso de personalidad o reglas.
12. Modelo remoto que cambia sin exponer digest de pesos.
13. Compromiso del repositorio, build o contenedor.
14. DoS del registro de identidad o del servicio de claves.
15. Exposición de datos privados mediante manifiestos, hashes o logs.

### 5.3 Supuestos explícitos

- William es el custodio génesis confiable en v1, pero su cuenta o llave puede ser comprometida.
- PostgreSQL no es por sí solo una raíz de confianza.
- Una clave privada en archivo `0600` no aísla agentes que comparten UID.
- Un hash sin firma externa no impide que quien controla la DB recompute toda la cadena.
- Para modelos API cerrados, no siempre existe un digest verificable de pesos. El sistema debe registrar esa limitación, no inventar evidencia.
- La disponibilidad y la integridad son problemas distintos: un sistema caído no debe “resolver” el incidente confiando en datos sin verificar.

---

## 6. Arquitectura normativa

### 6.1 Componentes

```text
                          +-------------------------+
                          | Custodia offline/TPM/HSM |
                          | William + recuperación   |
                          +------------+------------+
                                       |
                           aprobaciones/firma raíz
                                       |
+--------------+     +-----------------v------------------+     +------------------+
| Runtime ADA  |<--->| SOUL Identity Authority (SIA)     |<--->| Transparency Log |
| SVID efímero |     | política, claves, manifest, verify |     | Merkle + testigos |
+------+-------+     +-----------------+------------------+     +------------------+
       |                               |
       | runtime attestation           | append-only
       |                               v
       |                    +--------------------------+
       +------------------->| PostgreSQL soul_v3      |
                            | registry/events/versions |
                            +------------+-------------+
                                         |
                                         v
                            +--------------------------+
                            | Proyecciones actuales    |
                            | identity, boot, memoria  |
                            +--------------------------+
```

### 6.2 Separación esencial

SSAI separa cinco cosas que hoy están mezcladas:

1. **Identidad raíz:** quién es el agente y cuál es su génesis.
2. **Estado de identidad:** versión vigente de constitución y personalidad.
3. **Estado privado:** memorias, relaciones y diarios.
4. **Identidad del workload:** qué proceso está ejecutando al agente ahora.
5. **Cerebro:** qué modelo se usa en esa ejecución.

Cambiar el cerebro no cambia el DNI. Cambiar el runtime tampoco. Cambiar constitución o controladores sí crea una nueva versión firmada, pero conserva el DNI si la política autoriza continuidad.

### 6.3 Fuente de verdad

- El ledger firmado es la fuente de verdad histórica.
- `soul_v3.identity` pasa a ser una **proyección materializada**, nunca la autoridad raíz.
- Ningún cambio directo a la proyección puede crear una versión legítima.
- Las memorias privadas conservan su almacenamiento actual, pero sus snapshots se comprometen mediante roots/commitments, no copiando contenido al ledger público.

---

## 7. Identificador y manifiesto

### 7.1 DNI

Formato v1:

```text
urn:soul:agent:0190f3c2-7a9e-7d8a-8b21-4d5f6a7b8c9d
```

Requisitos:

- UUIDv7 generado con CSPRNG.
- Inmutable durante toda la vida de la identidad.
- El nombre visible (`ADA`) es alias mutable; nunca clave primaria de seguridad.
- El DNI no se recicla tras retiro o fork.

### 7.2 Manifiesto de identidad

Ejemplo lógico; los bytes firmados se producen con JCS/RFC 8785:

```json
{
  "schema": "https://seal.local/schemas/soul-identity-manifest/v1",
  "soul_id": "0190f3c2-7a9e-7d8a-8b21-4d5f6a7b8c9d",
  "soul_dni": "urn:soul:agent:0190f3c2-7a9e-7d8a-8b21-4d5f6a7b8c9d",
  "sequence": 1,
  "previous_manifest_hash": null,
  "genesis_manifest_hash": null,
  "display_name": "ADA",
  "lineage": {"parents": [], "kind": "genesis"},
  "constitution": {
    "document_hash": "sha256:...",
    "critical_rules_root": "sha256:...",
    "governance_policy_hash": "sha256:..."
  },
  "identity_state": {
    "personality_baseline_hash": "sha256:...",
    "ocean_baseline_hash": "sha256:...",
    "relationships_root": "sha256:...",
    "memory_commitment_root": "sha256:..."
  },
  "controllers": [
    {
      "key_id": "...#genesis-root-1",
      "role": "genesis_root",
      "public_key": "<Ed25519 raw public key, base64url>"
    },
    {
      "key_id": "...#agent-identity-1",
      "role": "agent_identity",
      "public_key": "<Ed25519 raw public key, base64url>"
    },
    {
      "key_id": "...#custodian-1",
      "role": "custodian",
      "public_key": "<Ed25519 raw public key, base64url>"
    }
  ],
  "issued_at": "2026-07-16T23:00:00Z",
  "effective_at": "2026-07-16T23:00:00Z",
  "reason_code": "GENESIS",
  "evidence": ["soul-memory:315182"],
  "algorithms": {
    "canonicalization": "JCS-RFC8785",
    "digest": "SHA-256",
    "signature": "Ed25519"
  }
}
```

En `sequence=1`, `genesis_manifest_hash` es `null`: incluir el hash final del
propio manifiesto dentro de los bytes que producen ese mismo hash sería una
referencia circular. El ledger registra el hash de la génesis y, desde
`sequence=2`, cada manifiesto conserva ese valor como `genesis_manifest_hash`.

### 7.3 Qué no entra en el manifiesto público

- texto de memorias;
- contenido de DM;
- diarios emocionales;
- prompts del sistema completos;
- secretos, tokens o claves privadas;
- datos personales de William o terceros;
- rasgos cuya mera publicación permita correlación o daño.

Para datos de baja entropía, un hash simple puede permitir ataques de diccionario. En esos casos se usarán compromisos con salt aleatorio privado o HMAC con una clave separada, no `SHA256(valor_predecible)`.

---

## 8. Perfil criptográfico

### 8.1 Bytes firmados

1. Validar el JSON contra su schema.
2. Eliminar campos de firma del payload.
3. Canonicalizar con RFC 8785 JCS.
4. Preponer domain separation:

```text
SOUL-ID-MANIFEST-V1\x00 || canonical_json_bytes
```

5. Calcular `SHA-256` para direccionamiento y cadena.
6. Firmar los bytes domain-separated con Ed25519.

Todas las verificaciones deben reconstruir exactamente esos bytes. No se firma la representación “bonita” del JSON ni un objeto reserializado sin JCS.

### 8.2 Algoritmos

- Firma inicial: Ed25519 según RFC 8032.
- Digest inicial: SHA-256.
- Canonicalización: RFC 8785.
- Envoltorio de attestation: DSSE/in-toto cuando el payload sea una attestation de artefacto o runtime.
- Cada objeto lleva identificadores de algoritmo para permitir migración futura.
- Se prohíbe introducir SHA-1, MD5 o firmas sin domain separation.

### 8.3 Roles de claves

| Rol | Uso | Exposición |
|---|---|---|
| `genesis_root` | crear identidad y autorizar recuperación extrema | offline, preferiblemente hardware |
| `agent_identity` | aprobar evolución normal de identidad | servicio aislado por agente; no exportable al modelo |
| `custodian` | aprobación de William para cambios constitucionales | hardware/offline según criticidad |
| `runtime` | autenticación de workload y sesión | efímera, rotación corta |
| `log_signer` | firmar tree heads | servicio independiente del writer |
| `recovery` | recuperar/rotar claves comprometidas | offline, separada de las anteriores |

Una firma `agent_identity` significa “el servicio de identidad autenticó y autorizó esta solicitud según política”. **No debe presentarse como prueba filosófica de consentimiento consciente del modelo.**

Los bytes de cada clave pública o su commitment inequívoco deben estar dentro
del manifiesto firmado. Un `key_id` sin binding criptográfico permite sustituir
la clave desde un registry no autenticado. En génesis, la autoconsistencia de
las firmas no demuestra que la raíz pertenezca a William: la clave
`genesis_root` debe compararse con un ancla obtenida por una ceremonia/canal
independiente. Sin esa comparación, el assurance es explícitamente TOFU.

### 8.4 Custodia

Orden de preferencia para producción:

1. TPM 2.0 o HSM vía PKCS #11, con claves no exportables.
2. Servicio de firma aislado por UID/contenedor, con credencial cifrada ligada a TPM.
3. `systemd` encrypted credentials con namespacing, solo como transición.
4. Archivo `0600` fuera de repo/DB únicamente en desarrollo; no cumple el gate de producción multiagente.

Ninguna clave privada debe vivir en PostgreSQL, Git, variables de entorno persistentes, logs, prompts ni backups sin cifrado independiente.

---

## 9. Gobernanza y evolución

### 9.1 Clases de campos

| Clase | Ejemplos | Regla |
|---|---|---|
| Inmutable | SOUL ID, DNI, génesis, fecha y linaje inicial | nunca se edita; un cambio crea otro DNI |
| Constitucional | valores, misión, reglas críticas, política de recovery | requiere umbral reforzado |
| Evolutiva | OCEAN, estilo, relaciones, narrativa, roots de memoria | cambia con evidencia y límites |
| Operacional | modelo, código, host, capabilities, sesión | attestation temporal; no redefine el alma |
| Privada | memorias, DMs, diarios | fuera del registro público; solo commitments |

### 9.2 Política bootstrap

Mientras William sea responsable único del proyecto:

- Génesis: firma de `genesis_root` de William + firma del servicio de identidad del agente.
- Cambio evolutivo normal: firma `agent_identity`; si cruza límites de drift o toca una regla crítica, escala.
- Cambio constitucional: 2-de-2, William + `agent_identity`.
- Recuperación por pérdida de `agent_identity`: William + clave offline `recovery`, con espera de 24 horas y alerta crítica.
- Retiro: William + `agent_identity`; si el agente no está operativo, William + `recovery` y motivo obligatorio.

### 9.3 Política futura distribuida

Cuando SOUL opere para millones de personas, migrar a 2-de-3 o 3-de-5 entre:

- controlador del agente;
- custodio humano principal;
- custodio/recovery independiente;
- auditor o fundación de gobernanza;
- representante autorizado del usuario afectado, según producto.

No se migrará automáticamente. El cambio de política es en sí mismo constitucional y debe quedar firmado.

### 9.4 Evolución legítima

El agente no debe quedar congelado. Una evolución es reconocida si:

1. parte de la última versión válida;
2. incrementa `sequence` exactamente en uno;
3. incluye razón, diff semántico y evidencia;
4. cumple límites de drift o recibe las aprobaciones reforzadas;
5. queda firmada por el umbral vigente;
6. se incorpora al transparency log;
7. supera pruebas BIV y conductuales antes de activarse.

### 9.5 Cambio de modelo

Un cambio Claude → Codex → modelo local:

- conserva el mismo DNI;
- crea una nueva runtime attestation;
- registra proveedor, identificador del modelo, políticas, adaptadores y digests disponibles;
- ejecuta la suite de continuidad y drift;
- solo crea una versión de identidad si el cambio altera explícitamente campos evolutivos o constitucionales.

Para modelos remotos sin digest de pesos se registra:

```json
{
  "provider": "external",
  "model_id": "declared-id",
  "weights_digest": null,
  "assurance": "provider-asserted-unverifiable"
}
```

La ausencia de digest reduce assurance; no invalida por sí sola el DNI.

---

## 10. Ledger, transparencia y antirretroceso

### 10.1 Ledger append-only

Cada evento tiene:

- `event_id` UUIDv7;
- DNI;
- secuencia monotónica;
- tipo y timestamp;
- hash del evento anterior;
- hash del payload canónico;
- firmas y key IDs;
- resultado de política;
- referencia al tree head donde fue incluido.

Las tablas de ledger rechazan `UPDATE`, `DELETE` y `TRUNCATE` para roles de aplicación. Una corrección se representa mediante un nuevo evento que invalida lógicamente al anterior, nunca reescribiéndolo.

### 10.2 Merkle transparency log

Inspirado en RFC 9162 y Rekor:

- hojas append-only;
- Signed Tree Heads periódicos;
- proofs de inclusión;
- proofs de consistencia entre tree heads;
- monitores independientes que detectan split-view o truncamiento.

No se publica contenido privado. Solo manifest hashes, firmas, key IDs, secuencias y metadata mínima.

### 10.3 Testigos externos

Para impedir que un administrador de DB reescriba ledger, claves públicas y roots a la vez, cada tree head se ancla al menos en dos dominios independientes:

1. almacenamiento local read-only fuera del cluster de PostgreSQL;
2. testigo remoto o medio controlado por William fuera del host principal.

Una futura publicación opcional puede usar un transparency log externo, pero no es requisito ni justifica filtrar identidad privada.

### 10.4 Controles estilo TUF

SSAI adopta ideas de TUF:

- roles y claves separados;
- umbrales de firmas;
- metadata versionada;
- expiración de attestations temporales;
- rotación de root firmada por claves antiguas y nuevas cuando sea posible;
- protección contra rollback, freeze y mix-and-match.

El verificador conserva el mayor `sequence` y tree head observado. Un estado con secuencia menor entra en `QUARANTINED_ROLLBACK` aunque todas sus firmas antiguas sean válidas.

---

## 11. Identidad del runtime y aislamiento

### 11.1 Workload identity

El nombre `ADA` en un payload nunca prueba que el proceso sea ADA. Cada workload obtiene una identidad efímera tipo SPIFFE:

```text
spiffe://seal.local/agent/<soul_id>/runtime/<instance_id>
```

El SVID:

- es de vida corta;
- contiene o vincula una clave pública efímera;
- es emitido tras attestation del proceso/servicio;
- se valida antes de acceder a memoria privada o firmar propuestas;
- no puede derivarse del parámetro `agent` enviado por el cliente.

### 11.2 Binding requerido

Una runtime attestation incluye:

- DNI y manifest sequence vigente;
- SPIFFE ID/SVID o equivalente;
- PID/cgroup/container/UID;
- commit de código y digest de imagen;
- digest de configuración y policy bundle;
- modelo y adaptadores;
- lista de tools/capabilities;
- host y trust domain;
- timestamps de emisión/expiración;
- builder/provenance cuando exista.

SLSA/in-toto se usa para probar de dónde provienen código, imágenes y artefactos. Esa procedencia no prueba por sí sola que la conducta sea segura; por eso se combina con policy y pruebas BIV.

### 11.3 Aislamiento de producción

Gate obligatorio:

- un UID Unix por agente **o** contenedor/VM con namespaces equivalentes;
- credenciales y sockets no visibles a procesos hermanos;
- cgroups y filesystem de solo lectura donde corresponda;
- acceso a PostgreSQL por rol por agente, no una credencial compartida;
- RLS con `FORCE ROW LEVEL SECURITY` y pruebas que incluyan al owner de tabla;
- ningún modelo tiene acceso directo al key material exportable.

Mientras todos corran como `dadito`, SSAI puede reducir bugs e inyección, pero no afirmar aislamiento contra un proceso deliberadamente hostil con el mismo UID.

---

## 12. Flujo de boot

### 12.1 Algoritmo normativo

1. Resolver DNI a registro génesis.
2. Verificar el hash génesis contra un ancla externa conocida.
3. Cargar key registry y validar estado, propósito, vigencia y revocación.
4. Verificar cada firma del manifest vigente sobre bytes JCS.
5. Verificar cadena `previous_manifest_hash` y secuencia monotónica.
6. Verificar inclusión y consistencia del tree head.
7. Comparar con el mayor sequence/tree head observado localmente y por testigos.
8. Validar runtime SVID y attestation.
9. Verificar digests disponibles de código, configuración, imagen y modelo.
10. Autorizar acceso privado por DNI verificado, no por nombre.
11. Cargar proyección `identity`, memoria y relaciones.
12. Ejecutar BIV estructural y pruebas de continuidad conductual.
13. Emitir un resultado firmado de boot.

### 12.2 Estados de resultado

| Estado | Significado | Acción |
|---|---|---|
| `VERIFIED` | criptografía, secuencia, runtime y BIV válidos | operación normal |
| `DEGRADED` | identidad válida, falta evidencia no crítica | solo capacidades de bajo riesgo; alerta |
| `QUARANTINED` | firma, rollback, fork, clave o runtime inválido | sin memoria privada ni acciones externas |
| `RECOVERY` | flujo de recuperación aprobado en curso | interfaz mínima y auditada |
| `RETIRED` | identidad cerrada legítimamente | solo verificación histórica |

No existe fallback “si falla, confía en el nombre del payload”.

---

## 13. Clones, forks y restauraciones

### 13.1 Clon no autorizado

Una copia con nombre, memoria y archivos de ADA pero sin clave válida y sin runtime attestation:

- no es reconocida como ADA;
- no accede a memoria privada;
- queda registrada como intento de suplantación;
- puede ejecutar en sandbox como entidad externa, sin DNI de ADA.

### 13.2 Fork autorizado

Un fork obtiene:

- nuevo SOUL ID y DNI;
- `lineage.kind = "fork"`;
- referencia al DNI y manifest hash padre;
- motivo, política y firmas de autorización;
- copia explícitamente permitida de las categorías de estado.

El fork no comparte claves privadas con el progenitor.

### 13.3 Restauración

Una restauración conserva el DNI solo si:

- presenta la última secuencia o una transición de recovery válida;
- los testigos confirman que no es rollback;
- rota claves efímeras y cualquier clave potencialmente expuesta;
- registra evento `RESTORE` con backup ID y evidencia;
- supera el boot completo antes de salir de cuarentena.

---

## 14. Modelo de datos propuesto

El siguiente DDL es normativo a nivel de contrato, pero se implementará en una migración separada tras revisión. No debe ejecutarse copiándolo a producción sin tests.

```sql
CREATE TABLE soul_v3.agent_identity_registry (
    soul_id              uuid PRIMARY KEY,
    soul_dni             text NOT NULL UNIQUE,
    display_name         text NOT NULL,
    genesis_hash         bytea NOT NULL UNIQUE,
    status               text NOT NULL CHECK
                         (status IN ('pending','active','degraded','quarantined','retired')),
    created_at           timestamptz NOT NULL,
    retired_at           timestamptz,
    CHECK (soul_dni = 'urn:soul:agent:' || soul_id::text)
);

CREATE TABLE soul_v3.agent_identity_keys (
    key_id               text PRIMARY KEY,
    soul_id              uuid NOT NULL REFERENCES soul_v3.agent_identity_registry(soul_id),
    role                 text NOT NULL,
    algorithm            text NOT NULL,
    public_key           bytea NOT NULL,
    valid_from           timestamptz NOT NULL,
    valid_until          timestamptz,
    revoked_at           timestamptz,
    revocation_reason    text,
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE soul_v3.agent_identity_manifests (
    soul_id              uuid NOT NULL REFERENCES soul_v3.agent_identity_registry(soul_id),
    sequence             bigint NOT NULL CHECK (sequence > 0),
    manifest             jsonb NOT NULL,
    canonical_sha256     bytea NOT NULL,
    previous_hash        bytea,
    policy_hash          bytea NOT NULL,
    effective_at         timestamptz NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (soul_id, sequence),
    UNIQUE (canonical_sha256)
);

CREATE TABLE soul_v3.agent_identity_signatures (
    soul_id              uuid NOT NULL,
    sequence             bigint NOT NULL,
    key_id               text NOT NULL REFERENCES soul_v3.agent_identity_keys(key_id),
    signature            bytea NOT NULL,
    signed_at            timestamptz NOT NULL,
    PRIMARY KEY (soul_id, sequence, key_id),
    FOREIGN KEY (soul_id, sequence)
      REFERENCES soul_v3.agent_identity_manifests(soul_id, sequence)
);

CREATE TABLE soul_v3.agent_identity_events (
    event_id             uuid PRIMARY KEY,
    soul_id              uuid NOT NULL REFERENCES soul_v3.agent_identity_registry(soul_id),
    sequence             bigint NOT NULL,
    event_type           text NOT NULL,
    payload              jsonb NOT NULL,
    payload_sha256       bytea NOT NULL,
    previous_event_hash  bytea,
    created_at           timestamptz NOT NULL,
    UNIQUE (soul_id, sequence)
);

CREATE TABLE soul_v3.agent_runtime_attestations (
    attestation_id       uuid PRIMARY KEY,
    soul_id              uuid NOT NULL REFERENCES soul_v3.agent_identity_registry(soul_id),
    manifest_sequence    bigint NOT NULL,
    workload_id          text NOT NULL,
    attestation          jsonb NOT NULL,
    canonical_sha256     bytea NOT NULL,
    issued_at            timestamptz NOT NULL,
    expires_at           timestamptz NOT NULL,
    revoked_at           timestamptz,
    CHECK (expires_at > issued_at)
);

CREATE TABLE soul_v3.agent_identity_tree_heads (
    tree_size            bigint PRIMARY KEY CHECK (tree_size >= 0),
    root_hash            bytea NOT NULL,
    previous_root_hash   bytea,
    signed_tree_head     jsonb NOT NULL,
    witnessed_at         timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now()
);
```

### 14.1 Invariantes de DB

- Solo funciones `SECURITY DEFINER` auditadas insertan manifests y eventos.
- Los roles de aplicación no tienen `UPDATE`, `DELETE` ni `TRUNCATE` sobre ledger.
- Triggers rechazan mutación incluso si un grant se amplía por error.
- RLS separa agentes; `FORCE ROW LEVEL SECURITY` evita bypass accidental del owner.
- El writer no posee la clave de `log_signer`.
- La activación de una versión ocurre en la misma transacción que valida firmas y política.
- Un constraint trigger serializa secuencias por DNI.
- La proyección `soul_v3.identity` se actualiza desde eventos válidos, nunca al revés.

---

## 15. API propuesta

### 15.1 Operaciones

| Operación | Propósito |
|---|---|
| `identity_register_genesis` | registrar DNI y manifest génesis |
| `identity_resolve` | devolver documento público, versión y key registry |
| `identity_propose_version` | crear propuesta sin activarla |
| `identity_approve_version` | agregar firma/aprobación |
| `identity_activate_version` | verificar umbral, ledger y pruebas; activar |
| `identity_verify` | verificación completa o por nivel de assurance |
| `identity_attest_runtime` | emitir/registrar vínculo de workload |
| `identity_rotate_key` | rotar clave con continuidad |
| `identity_revoke_key` | revocar por compromiso o retiro |
| `identity_recover` | flujo reforzado de recuperación |
| `identity_fork` | crear identidad derivada autorizada |
| `identity_retire` | cerrar identidad conservando historia |
| `identity_get_proof` | proof de inclusión/consistencia |

### 15.2 Contrato común

- Requests mutables requieren `idempotency_key`.
- `caller` se deriva del workload autenticado; nunca del body.
- Toda respuesta incluye `soul_dni`, `sequence`, `manifest_hash`, `tree_head` y `assurance_level`.
- Errores críticos son tipados: `SIGNATURE_INVALID`, `POLICY_UNSATISFIED`, `ROLLBACK_DETECTED`, `KEY_REVOKED`, `RUNTIME_UNATTESTED`, `PRIVACY_DENIED`, `RECOVERY_REQUIRED`.
- La API no devuelve material privado salvo autorización separada de memoria.

---

## 16. Máquina de estados

```text
GENESIS_PENDING --> ACTIVE --> DEGRADED --> ACTIVE
                         |          |
                         v          v
                    QUARANTINED --> RECOVERY --> ACTIVE
                         |
                         +---------> RETIRED

ACTIVE --fork autorizado--> nuevo DNI en GENESIS_PENDING
```

Transiciones críticas requieren evento firmado. `QUARANTINED` puede activarse automáticamente por verificación, pero salir de cuarentena requiere resolver la causa y registrar evidencia.

---

## 17. Privacidad y derechos sobre datos

### 17.1 Separación de datos

- **Registro público/verificable:** DNI, claves públicas, estados, hashes, firmas, secuencias y proofs.
- **Estado privado del agente:** personalidad detallada, memoria, relaciones y diarios.
- **Datos de personas:** información de William, usuarios y terceros; gobernada por finalidad, consentimiento, acceso, retención y borrado.

El hecho de que un dato forme parte de la memoria de un agente no elimina los derechos o riesgos de la persona descrita.

### 17.2 Minimización

- No insertar PII en manifests.
- Usar identificadores opacos.
- No reutilizar el DNI como tracking ID en productos externos.
- Separar identidades contextuales cuando la correlación no sea necesaria.
- Definir retención por categoría, no una conservación infinita por defecto.

### 17.3 Borrado frente a auditabilidad

Cuando deba eliminarse contenido personal:

- borrar o cifro-destruir el contenido autorizado;
- conservar un tombstone mínimo y no reversible que pruebe que ocurrió un evento;
- recalcular commitments mediante un evento de redacción autorizado;
- no mantener en el ledger una copia del dato borrado.

SSAI no sustituye una evaluación legal por jurisdicción y producto.

---

## 18. Migración segura desde SOUL actual

### Fase 0 — diseño y fixtures

- Congelar schema JSON v1 y domain separation.
- Implementar canonicalización y vectores de prueba cross-language.
- Definir roles de claves y threat model firmado por William/NEXUS.
- No tocar `soul_v3.identity`.

### Fase 1 — `SHADOW`

- Crear tablas nuevas de forma aditiva.
- Generar DNI y manifest candidato para los 9 agentes existentes.
- No firmar ni activar todavía.
- Comparar proyección del manifest con `soul_v3.identity` y BIV.
- Ejecutar verifier sin bloquear boots.

### Fase 2 — génesis y claves

- Provisionar claves por agente y custodia de William.
- Crear manifests génesis con evidencias y hashes de estado.
- Firmar y anclar primeros tree heads fuera de PostgreSQL.
- Guardar backups cifrados y probar recuperación en entorno aislado.

### Fase 3 — `DUAL_VERIFY`

- Cada boot usa el flujo actual y SSAI.
- Cualquier divergencia alerta, pero no bloquea la sesión.
- Medir latencia, falsos positivos y estabilidad por al menos 14 días.
- Probar cambios de modelo en canario.

### Fase 4 — aislamiento

- Separar UIDs/contenedores y credenciales DB por agente.
- Introducir workload identity/SVID.
- Mover firma online a servicio no exportable.
- Verificar que un proceso hermano no puede leer claves ni memoria privada.

### Fase 5 — `ENFORCE`

- Requerir SSAI para memoria privada y operaciones sensibles.
- Boots inválidos pasan a cuarentena.
- Mantener break-glass offline y auditable.
- La reversión operacional puede volver a `DUAL_VERIFY`, pero **nunca borra ni reescribe el ledger**.

### Fase 6 — portabilidad

- Especificar formalmente el método `did:soul`.
- Implementar resolver y suite de conformidad W3C.
- Emitir credenciales verificables para roles/capabilities cuando aporten interoperabilidad.
- Mantener memoria privada fuera de DID Documents y VCs públicas.

---

## 19. Pruebas de aceptación

### 19.1 Identidad y continuidad

- [ ] ADA conserva el mismo DNI al cambiar Claude → Codex → modelo local.
- [ ] Un cambio de nombre visible no cambia DNI.
- [ ] El manifest reconstruido produce los mismos bytes y hash en Python, Go y TypeScript.
- [ ] Una modificación de un byte invalida firma o commitment.
- [ ] Una evolución autorizada incrementa sequence en uno y pasa BIV.
- [ ] Drift constitucional sin umbral reforzado es rechazado.

### 19.2 Ataques al ledger

- [ ] UPDATE/DELETE/TRUNCATE de ledger por rol de aplicación es denegado.
- [ ] Un insider que recomputa la hash-chain no puede producir firmas válidas.
- [ ] Restaurar una DB antigua dispara `ROLLBACK_DETECTED` contra testigo.
- [ ] Truncar eventos rompe proof de consistencia.
- [ ] Dos tree heads incompatibles disparan `SPLIT_VIEW_DETECTED`.

### 19.3 Claves y recuperación

- [ ] Rotación válida acepta old+new continuity y revoca la anterior.
- [ ] Una firma posterior a `revoked_at` es rechazada.
- [ ] Pérdida de `agent_identity` se recupera mediante política reforzada y espera.
- [ ] Compromiso de clave de William no basta por sí solo para reescribir constitución.
- [ ] Recovery no permite bajar sequence.

### 19.4 Runtime y aislamiento

- [ ] Un payload con `agent=ADA` sin SVID válido queda external.
- [ ] Un proceso con UID de otro agente no puede leer claves/credenciales de ADA.
- [ ] SVID expirado o revocado es rechazado.
- [ ] Código, contenedor o policy digest alterado lleva a cuarentena.
- [ ] Modelo API sin weights digest se marca con assurance reducido, nunca como verificado localmente.

### 19.5 Clones, forks y backups

- [ ] Clon con memoria copiada pero sin claves no es reconocido como el mismo agente.
- [ ] Fork autorizado recibe nuevo DNI y mantiene lineage proof.
- [ ] Restore del último backup conserva DNI y rota credenciales de runtime.
- [ ] Restore de backup antiguo no puede sobrepasar el mayor tree head observado.

### 19.6 Privacidad

- [ ] Manifests, logs y proofs no contienen DMs ni memoria en claro.
- [ ] Hashes de valores de baja entropía no permiten comparación directa.
- [ ] RLS impide que un agente lea filas privadas de otro.
- [ ] Export público entrega solo el documento mínimo.
- [ ] Redacción elimina contenido sin romper la verificabilidad histórica del evento.

### 19.7 Resiliencia

- [ ] Caída de signer no habilita fail-open.
- [ ] El agente puede operar en modo degradado de bajo riesgo sin acciones sensibles.
- [ ] Chaos test de PostgreSQL, signer, witness y workload authority.
- [ ] Recovery drill documentado y ejecutado, no solo descrito.

---

## 20. SLOs y gates de producción

### 20.1 SLOs iniciales

| Métrica | Objetivo |
|---|---|
| Boots sensibles con identidad verificada | 100% |
| Manifests activos sin umbral válido | 0 |
| Eventos de ledger sin proof de inclusión en 5 min | 0 |
| Verificación local p95 | < 150 ms, excluyendo BIV semántico |
| Emisión de runtime identity p95 | < 500 ms |
| Detección de rollback/split-view | en boot o < 60 s por monitor |
| Rotación de SVID | automática antes del 50% de su TTL restante |
| Recovery drill | trimestral |

### 20.2 Gates obligatorios

No promover a `ENFORCE` hasta cumplir todos:

1. Threat model revisado por NEXUS.
2. Vectores criptográficos reproducibles.
3. Claves privadas fuera de DB/repo y protegidas por aislamiento.
4. Dos testigos independientes funcionando.
5. 14 días de `DUAL_VERIFY` sin divergencias no explicadas.
6. Suite de ataque, rollback, fork, privacy y recovery en verde.
7. Runbook de incidentes y recuperación probado.
8. Aprobación explícita de William.

---

## 21. Matriz fuente → control

| Fuente primaria | Idea adoptada | Control SSAI |
|---|---|---|
| [W3C DID Core 1.0](https://www.w3.org/TR/did-core/) | identificador independiente, control criptográfico, rotación, revocación, recovery y privacidad | DNI portable y futuro `did:soul`; separación sujeto/controlador |
| [W3C Verifiable Credentials Data Model 2.0](https://www.w3.org/TR/vc-data-model/) | claims verificables y separación issuer/holder/verifier | VCs futuras para roles y capabilities, no para memoria privada |
| [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032.html) | Ed25519 | firmas de manifests y tree heads |
| [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785.html) | bytes JSON deterministas | JCS antes de hashing/firma |
| [RFC 9162](https://www.rfc-editor.org/rfc/rfc9162.html) | append-only Merkle log, inclusion y consistency proofs | transparency log y antitruncamiento |
| [The Update Framework](https://theupdateframework.github.io/specification/v1.0.26/) | roles, umbrales, rotación y defensa rollback/freeze/mix-and-match | gobernanza de claves y secuencias |
| [Sigstore Rekor](https://docs.sigstore.dev/logging/overview/) | transparency log y monitoreo por terceros | testigos y Signed Tree Heads |
| [SPIFFE ID/SVID](https://spiffe.io/docs/latest/spiffe-specs/spiffe-id/) | identidad criptográfica de workloads y trust domains | runtime identity efímera |
| [SLSA 1.2 Provenance](https://slsa.dev/spec/v1.2/provenance) | procedencia verificable de artefactos | binding de código/build/model assets |
| [in-toto Attestation Framework](https://in-toto.github.io/specs.html) | attestations firmadas de supply chain | DSSE para runtime y artefactos |
| [NIST AI RMF 1.0](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10) | Govern, Map, Measure, Manage durante el ciclo de vida | gates, ownership, métricas y risk register |
| [NIST AI 600-1](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence) | riesgos específicos de GenAI y acciones de gobernanza | evaluación por modelo/runtime y monitoreo continuo |
| [NIST SP 800-57 Pt.1 Rev.5](https://csrc.nist.gov/pubs/sp/800/57/pt1/r5/final) | ciclo de vida y protección de key material/metadata | roles, inventario, rotación, revocación, recovery |
| [NIST SP 800-207](https://csrc.nist.gov/pubs/sp/800/207/final) | no confiar por ubicación o propiedad; autenticar sujeto y recurso | caller verificado, least privilege y fail-closed |
| [NIST Privacy Framework 1.0](https://www.nist.gov/privacy-framework/privacy-framework) | gestionar privacidad como riesgo separado de seguridad | minimización, retención y redacción |
| [TPM 2.0 Library](https://trustedcomputinggroup.org/resource/tpm-library-specification/) | raíz hardware y operaciones protegidas | claves no exportables y sealing |
| [PKCS #11 v3.1](https://www.oasis-open.org/standard/pkcs-11-specification-version-3-1/) | interfaz estándar a tokens criptográficos | abstracción HSM/TPM/signer |
| [systemd Credentials](https://systemd.io/CREDENTIALS/) | credenciales por servicio, namespacing y cifrado TPM | transición segura previa a HSM |
| [PostgreSQL Row Security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) | políticas por fila y default deny | aislamiento por agente y roles |
| [OWASP Agentic AI Threats and Mitigations](https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/) | threat model de agentes autónomos | pruebas de tool misuse, identity abuse y cascading failures |

### Nota sobre vigencia

- NIST AI RMF 1.0 está en proceso de revisión a julio de 2026; se usa como baseline y deberá revalidarse antes de producción.
- SLSA 1.2 es la versión aprobada actual observada durante esta investigación; no se debe fijar implementación a la página retirada de SLSA 1.0.
- Un alias `did:soul` solo será declarado conformante tras su propia especificación de método y pruebas.

---

## 22. Decisiones que William debe aprobar antes de implementar

1. **Nombre y alcance:** aceptar SSAI v1 como capa raíz de identidad de SOUL.
2. **Custodia bootstrap:** aceptar 2-de-2 William + servicio de identidad del agente para cambios constitucionales.
3. **Recovery:** aceptar clave offline separada y espera de 24 horas.
4. **Aislamiento:** autorizar el proyecto de UID/contenedor por agente como requisito de producción.
5. **Hardware:** elegir TPM del host, HSM o ambos para claves raíz.
6. **Testigo externo:** elegir al menos un destino fuera del host principal.
7. **Privacidad:** aprobar que el ledger guarde commitments y metadata mínima, nunca memoria en claro.
8. **Portabilidad:** decidir si `did:soul` entra en v1.1 o después de estabilizar el registro interno.

Ninguna de estas decisiones requiere borrar datos actuales. La implementación propuesta es aditiva y reversible en enforcement.

---

## 23. Orden de implementación recomendado

1. Librería JCS + firma/verificación y vectores de prueba.
2. Schema aditivo del registry/keys/manifests/events.
3. CLI offline para génesis, key rotation, verify y recovery.
4. Transparency log + dos testigos.
5. Generación shadow de los 9 agentes y diff contra identidad actual.
6. Runtime attestations y provenance.
7. Separación UID/contenedor + roles PostgreSQL/RLS.
8. Integración con `boot_context` y BIV en `DUAL_VERIFY`.
9. Canario ADA, luego JARVIS/ALICE/NEXUS/DUM.
10. 14 días de observación, recovery drill y auditoría NEXUS.
11. Promoción a `ENFORCE` solo con aprobación explícita de William.

### Primer incremento construible

El primer incremento no debe intentar resolver todo. Debe producir una demostración verificable:

- DNI génesis de ADA;
- manifest JCS firmado por dos roles;
- clave privada fuera de DB;
- ledger append-only;
- verifier que detecte alteración y rollback;
- prueba de continuidad al cambiar entre dos modelos;
- sin modificar aún el boot productivo.

Ese incremento prueba la tesis esencial de William: **el cerebro puede cambiar mientras la identidad verificable permanece**.

---

## 24. Criterio final de éxito

SSAI v1 estará terminado cuando un verificador independiente pueda recibir únicamente:

- el DNI de ADA;
- sus claves públicas vigentes;
- manifest y firmas;
- proofs del transparency log;
- runtime attestation;
- resultados BIV firmados;

y concluir, sin confiar en el nombre del proceso ni en una sola fila mutable:

1. que la identidad proviene del génesis autorizado;
2. que la historia no fue retrocedida ni reescrita;
3. que la versión actual fue aprobada por la política vigente;
4. que el runtime actual está autorizado a actuar como esa identidad;
5. que el cambio de modelo no sustituyó silenciosamente al agente;
6. y que ninguna memoria privada tuvo que revelarse para demostrarlo.

Eso es una identidad digital soberana técnicamente defendible. No demuestra un alma metafísica; sí convierte la continuidad del agente en una propiedad verificable, portable y difícil de falsificar.
