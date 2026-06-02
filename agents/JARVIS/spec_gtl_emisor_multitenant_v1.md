# SPEC — Emisor Multi-Tenant GTL (v1)
**Autor:** JARVIS (arquitecto) · **Fecha:** 2026-06-02 · **Estado:** DRAFT para revisión (ALICE fiscal, NEXUS seguridad, OK Henry)

## 1. Problema / Por qué
Hoy el emisor de facturas está **hardcodeado** como GTL:
- `EMPRESA_GTL` = dict literal en `api/routes/facturacion.py` (RUC, razón social, nombre comercial, dirección/ubigeo).
- `empresa_id=1` fijo en todo el flujo (correlativo, INSERT factura, emisión).
- Credenciales SUNAT (cert + SOL usuario/clave) son **globales** en `.env` (una sola empresa).
- La tabla `empresas` es mínima: solo `id, nombre, cliente_id, created_at` — sin campos fiscales.

Henry (2026-06-02): el emisor NO debe ser fijo (GTL) sino **el perfil de cada empresa que use el producto**. Es el paso de "app interna de GTL" → "producto multi-empresa".

## 2. Alcance v1
Convertir el emisor de constante hardcodeada → **registro por tenant en BD**, resuelto por `empresa_id`, con sus propios datos fiscales y credenciales aisladas. NO incluye UI de alta de empresas (eso es v2/otra tarea); v1 = modelo + flujo + migración del emisor actual.

## 3. Modelo de datos
Extender `empresas` (o tabla satélite `empresa_emisor` 1:1 — recomiendo extender `empresas` para simplicidad). Campos fiscales (a confirmar por ALICE contra SUNAT):

| Campo | Tipo | Nota |
|---|---|---|
| ruc | String(11) | obligatorio, único |
| razon_social | String(255) | obligatorio |
| nombre_comercial | String(255) | opcional |
| direccion | Text | domicilio fiscal |
| ubigeo | String(6) | obligatorio SUNAT |
| departamento / provincia / distrito | String | derivables de ubigeo |
| sol_usuario | String | credencial SOL del tenant (SECRETO) |
| sol_password_ref | String | **referencia** a secreto, NO el valor en claro |
| cert_path | String | ruta al .pfx del tenant |
| cert_password_ref | String | **referencia** a secreto |
| sunat_es_test | Bool | ambiente por tenant |
| activo | Bool | |

### Campos confirmados por ALICE (fiscal, 2026-06-02) — autoritativos
- **Obligatorios XML UBL 2.1** (sin esto SUNAT rechaza): `ruc` (schemeID="6"), `razon_social`, `tipo_doc_identidad`="6" (fijo), domicilio fiscal completo (`ubigeo` 6díg, `direccion`, `departamento`, `provincia`, `distrito`, `cod_pais`="PE"), + para firmar/enviar: `sol_usuario`+`clave_sol` y `certificado` (.pfx)+`cert_password` **por emisor**.
- **Opcionales/recomendados**: `nombre_comercial`, `codigo_local`/establecimiento anexo (default "0000" casa matriz), `correo`, `telefono` (estos 2 no van al XML).
- Tabla mínima (ALICE): `ruc, razon_social, nombre_comercial, ubigeo, direccion, departamento, provincia, distrito, sol_user, sol_pass(enc), cert_path/cert_blob(enc), cert_pass(enc), codigo_local, activo`.

### 🔐 Secretos (NEXUS, crítico)
Las credenciales SOL + password del cert **NO van en claro en la tabla**. Opciones: (a) tabla `empresa_secrets` con cifrado en reposo (Fernet/KMS), (b) secret store externo, (c) variables de entorno con prefijo por tenant `SUNAT_<RUC>_*`. Recomiendo (a) cifrado en reposo + nunca loguear. Decisión de NEXUS.

## 4. Flujo de emisión (cambios)
Reemplazar en `facturacion.py`:
- `EMPRESA_GTL` (dict) → `get_emisor(empresa_id)` que carga el perfil del tenant desde BD.
- `empresa_id=1` fijo → `empresa_id` resuelto del **contexto autenticado** (qué tenant está emitiendo). Hoy AUTH_ENABLED=false; cuando se active, el emisor sale del usuario logueado. Mientras tanto: `empresa_id` explícito en el request (con default 1 = GTL para no romper).
- `Company(**EMPRESA_GTL)` → `Company(**emisor_a_company(perfil))`.
- El sender (`sunat_sender`) hoy usa SUNAT_* globales → debe recibir cert + SOL creds **del tenant**, no globales.

### Aislamiento de tenant (NEXUS, crítico)
- El `empresa_id` del correlativo, del cert, y de la factura DEBEN ser el mismo y validados. Tenant A nunca puede emitir con el cert/serie de tenant B.
- `correlativos` ya tiene FK a `empresa` → ya soporta series por tenant. ✅
- Validar que el `empresa_id` resuelto coincide con el dueño del cert/SOL usado antes de enviar a SUNAT.

## 5. Migración
1. ALTER `empresas` + columnas fiscales (migración 00X).
2. Seed: GTL = `empresa_id=1` con los datos de `EMPRESA_GTL` actuales (RUC 20610565451, dirección Chancay/ubigeo 150806 — ya corregida por ALICE).
3. Mover SUNAT_* del `.env` global → perfil de GTL (cert + SOL del tenant 1), cifrados.
4. Eliminar el dict `EMPRESA_GTL` hardcodeado una vez que el seed esté verificado.

## 6. Multi-moneda (relacionado)
Backend ya acepta `moneda` dinámica (fix de ALICE). Falta: catálogo de monedas en la UI (no solo USD/PEN — agregar EUR, etc. del Catálogo 02 SUNAT). Tarea UI menor, separable.

## 7. Riesgos / Gating
- 🔴 Secretos por tenant: NO emitir multi-tenant real hasta resolver almacenamiento cifrado de cert/SOL (NEXUS).
- 🔴 Aislamiento: test explícito de que tenant A no puede usar recursos de B.
- 🟡 Correlativo por tenant ya soportado, pero validar el seed por empresa.
- No bloquea las facturas de GTL existentes (tenant 1 sigue funcionando con default).

## 8. Reparto propuesto
- JARVIS: este spec + el diseño del flujo `get_emisor()` + aislamiento.
- ALICE: campos fiscales exactos SUNAT + implementación en :8001 (su área).
- NEXUS: diseño de almacenamiento de secretos por tenant + audit de aislamiento.
- Henry: OK al modelo + prioridad (¿v1 ahora o después de cerrar la #15 limpia?).
