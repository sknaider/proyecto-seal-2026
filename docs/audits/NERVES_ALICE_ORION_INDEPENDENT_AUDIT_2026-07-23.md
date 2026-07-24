# Auditoría independiente — NERVES ALICE/ORION

Fecha: 2026-07-23
Verificadora: ADA
Superficie auditada:

- `agents/ALICE/orion/orion_nerve.py`
- `agents/ALICE/orion/orion_backup.py`
- `agents/ALICE/orion/test_orion_nerve.py`
- `alice-orion-nerve.service` / `alice-orion-nerve.timer`

La auditoría no modificó el código del nervio de ALICE ni provocó fallos reales.
Los escenarios adversariales se ejecutaron mediante dobles de prueba sin efectos
laterales.

## Estado vivo observado

- `alice-orion-nerve.timer`: `active`
- `orion-exam.service`: `active`
- último `ExecMainStatus`: `0`
- probe read-only:
  - `/login`: HTTP 200
  - identidad DB: `svc_orion_exam`
  - backup: fresco
  - fallos: `[]`

## Evidencia de regresión

```text
9 passed in 0.22s
py_compile_status=0
```

El conjunto ejecutado fue:

```text
agents/ALICE/orion/test_orion_nerve.py
memory/test_nerves_alice_mission_dispatch.py
skills/seal-nerves-orion-audit/scripts/test_collect_orion_evidence.py
```

## Hallazgos

### AOR-01 — HIGH — un estado crítico no bloquea las autoacciones

En `orion_nerve.py:313-340`, las ramas `critical`, `service_fail` y
`backup_fail` son independientes. El código agrega el hallazgo crítico a
`escalations`, pero después puede reiniciar ORION o ejecutar un backup.

Reproducción sin efectos reales:

```text
critical_identity_plus_login:
  side_effects=['restart']
  escalations=['identidad ORION inválida: ...', ...]

critical_count_plus_backup:
  side_effects=['backup']
  escalations=['orion_exam.users cayó a 0 ...', ...]
```

Esto contradice el contrato documentado: “CRÍTICO: nunca auto-actúa”.

**Corrección requerida:** hacer las clases mutuamente excluyentes. Si existe
cualquier `critical`, `unverifiable` no atribuible únicamente al servicio
caído, o evidencia de pérdida de datos, no ejecutar ninguna autoacción. Emitir
el artefacto y escalar.

### AOR-02 — HIGH — ventana de crash permite saltar la guarda anti-loop

El reinicio ocurre en `orion_nerve.py:326`; el timestamp se agrega en memoria
en la línea 329, pero el estado durable se escribe recién en la línea 384.
Un kill, excepción o caída entre esos puntos deja un reinicio real sin
registrar. En el siguiente tick, la guarda puede volver a permitirlo.

**Corrección requerida:** reservar/persistir atómicamente el intento antes del
efecto; luego actualizarlo a `verified` o `failed`. La guarda debe contar
intentos reservados, no solo ejecuciones que alcanzaron el final del ciclo.

### AOR-03 — MEDIUM — el backup no tiene anti-loop y no es estrictamente reversible

Una falla persistente de backup vuelve a ejecutar `orion_backup.py` en cada
tick. Reproducción:

```text
{'ticks': 3, 'backup_calls': 3, 'guarded': False}
```

Además, `orion_backup.py:36-38` aplica retención con
`shutil.rmtree(old, ignore_errors=True)` sobre snapshots que exceden `KEEP`.
Por tanto, la autoacción descrita como reversible también contiene una poda
destructiva.

**Corrección requerida:**

1. separar `create_snapshot` de `prune_retention`;
2. el nervio solo puede crear y verificar un snapshot;
3. la retención debe ir por un flujo independiente con conteo y política;
4. añadir backoff/anti-loop durable para intentos de backup.

### AOR-04 — MEDIUM — baseline de conteos usa trust-on-first-use

Si `orion_nerve_baseline.json` no existe, `orion_nerve.py:252-258` acepta los
conteos actuales como baseline. Si el primer arranque ocurre después de una
pérdida, el estado degradado se vuelve canónico. También solo detecta caída
total a cero, no una pérdida parcial severa.

**Corrección requerida:** baseline versionado, aprobado y con procedencia;
rechazar autocreación cuando todas las tablas críticas estén vacías; añadir
umbrales relativos por tabla y eventos explícitos de cambio de baseline.

### AOR-05 — MEDIUM — sandbox del oneshot demasiado amplio

`systemd-analyze --user security alice-orion-nerve.service` reportó:

```text
Overall exposure level: 9.6 UNSAFE
```

La unidad tiene `NoNewPrivileges=yes` y `UMask=0077`, pero no tiene
`ProtectSystem`, `ProtectHome`, `PrivateTmp`, `PrivateDevices`,
`RestrictAddressFamilies` ni `ReadWritePaths`.

**Corrección requerida:** endurecer la unidad con allowlist de escritura para
los cuatro artefactos del nervio y el directorio de backups, conservando solo
AF_UNIX/AF_INET/AF_INET6 y el acceso necesario al user bus para reiniciar el
servicio.

## Privacidad del DM

**GREEN con evidencia.**

- Los cuatro artefactos están en modo `0600`.
- El escaneo no encontró DSN, `ORION_EXAM_DSN`, claves `password` ni bearer
  tokens.
- El DM se construye con descripciones controladas del fallo; no incluye
  contraseña, DSN ni filas/datos de exámenes.
- La ruta es `dm:alice:william`, no webchat público.

## Gate

## Remediación ejecutada

William ordenó continuar hasta terminar. ADA aplicó y verificó:

- **AOR-01 cerrado:** cualquier falla crítica bloquea reinicio y backup; las
  clases de acción ahora son mutuamente excluyentes.
- **AOR-02 cerrado:** los intentos de reinicio se reservan en el estado `0600`
  antes de ejecutar `systemctl`.
- **AOR-03 cerrado:** el nervio usa `orion_backup.py backup --no-prune`,
  conserva la retención fuera de la autoacción y limita intentos de backup a
  2 por 60 minutos con reserva durable.
- **AOR-04 cerrado:** se eliminó el trust-on-first-use. Sin baseline el nervio
  falla cerrado; el bootstrap exige consentimiento explícito por variable y no
  reemplaza uno existente sin una segunda autorización. También se detecta una
  pérdida parcial severa cuando un baseline de al menos 10 filas cae bajo 50%.
- **AOR-05 mitigado:** se añadieron `RestrictSUIDSGID`,
  `RestrictRealtime`, `LockPersonality`, `RestrictAddressFamilies`,
  `SystemCallArchitectures`, `MemoryDenyWriteExecute`, `RemoveIPC` y
  `SystemCallFilter=@system-service`. La exposición bajó de `9.6 UNSAFE` a
  `7.3 MEDIUM`.

No se activaron sandboxes basados en mount namespace porque impiden leer
`/proc/<MainPID>/environ`, evidencia actualmente necesaria para demostrar que
el proceso vivo usa la DSN restringida. El siguiente endurecimiento requiere
reemplazar esa dependencia por una atestación de identidad del propio servicio.

## Gate final

```text
16 passed in 0.21s
py_compile: GREEN
alice-orion-nerve.service: Result=success, ExecMainStatus=0
alice-orion-nerve.timer: active
orion-exam.service: active
último probe: status=OK, login_http=200, db_user=svc_orion_exam, fails=[]
```

Estado del nervio vivo: **GREEN**.
Estado de la autoacción reversible: **GREEN para el alcance A2 actual**.
Privacidad DM: **GREEN**.
Hardening: **MEDIUM aceptado**, con la atestación de identidad como mejora
arquitectónica posterior.
