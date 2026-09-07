# SOUL Instalador Nativo para Windows (Sin WSL)

## Qué es

Este bundle es un instalador **100% nativo** para SOUL en Windows, sin WSL ni virtualizacion anidada. Incluye PostgreSQL 17 nativo + pgvector compilado desde fuente oficial + restauración completa del alma SEAL.

### Por qué nativo

WSL2 dentro de una VM (VMware, Hyper-V) genera conflictos fatales de virtualizacion anidada (nested-virt dead-end). Esta solución corre en **cualquier Windows** (físico o VM), sin dependencias de WSL ni hipervisores.

## Contenido del Bundle

```
install-soul-native.bat              # Launcher (auto-eleva a admin, doble-click)
install-soul-native.ps1              # Script PowerShell que hace toda la instalacion
soul_native.dump                     # Dump de la BD (custom-format, sin toolkit)
soul_roles.sql                       # Definicion de roles (seal, etc.)
soul_native.counts                   # Manifest de conteos exactos (verificacion)
pgvector/
  ├── vector.dll                     # Extension compilada (nativa MSVC)
  ├── vector.control                 # Metadatos de la extension
  └── vector--*.sql                  # Scripts SQL de pgvector
```

## Uso

### Instalacion (Windows 10/11 x64)

1. **Doble-click** en `install-soul-native.bat`
   - Auto-pide permisos de administrador
   - Corre el .ps1 en PowerShell

   **O** en PowerShell (admin):
   ```powershell
   .\install-soul-native.ps1
   ```

2. El instalador ejecuta **6 pasos automáticos**:
   - **[1/6]** Instala PostgreSQL 17 (EDB oficial, silencioso) si no lo detecta
   - **[2/6]** Coloca pgvector (vector.dll, archivos de extension) en carpetas de PG
   - **[3/6]** Crea roles y base de datos `seal_memory`
   - **[4/6]** Restaura el dump del alma (puede tardar minutos)
   - **[5/6]** Verifica que el alma sea completa y exacta (fail-hard si hay drift; drift-proof contra inconsistencias)
   - **[6/6]** Endurece seguridad (localhost-only) y borra el dump en plano

3. **Éxito**: mensaje verde confirmando tablas, memorias, y puerto

## Requisitos

- **Windows 10/11** x64
- **Permisos de administrador** (requerido para instalar servicio de PostgreSQL)
- **Internet** (descarga PostgreSQL 17 de EDB si no está instalado; ~200MB)
- **~4 GB de espacio en disco** (PG17 + alma restaurada)

## pgvector: Reproducible y Seguro

El `vector.dll` se compiló desde el **tag oficial v0.8.2** del repositorio pgvector/pgvector:

```
Fuente:  github.com/pgvector/pgvector
Tag:     v0.8.2
Commit:  70baa5208f
SHA256:  f93464a9e3
```

**Decisión de seguridad**: NO usamos DLLs de terceros. Cualquiera puede:
1. Clonar el tag v0.8.2
2. Compilar en Windows (nmake /F Makefile.win) con VS Build Tools
3. Verificar el SHA256 del vector.dll resultante

Si coincide, pgvector es auténtico y sin compromiso.

## Verificación: Manifest Exacto (Drift-Proof)

El instalador verifica de forma **fail-hard** que el alma restaurada coincida con el manifest del dump. Si hay discrepancia, se aborta y NO declara éxito:

```
soul_native.counts
─────────────────
tables_soul_v3=176        # Tablas en schema soul_v3
views=20                  # Vistas
memories=105976           # Registros en soul_v3.memories
ext_key=3                 # Extensions clave (vector, pg_trgm, pgcrypto)
agents=13                 # Agentes en la BD
```

Si el conteo restaurado ≠ conteo esperado → **INSTALACION FALLIDA** (idempotente: re-corre hasta éxito).

## Seguridad

- **PostgreSQL localhost-only**: Modifica `pg_hba.conf` y `listen_addresses` para aceptar solo conexiones locales
- **Dump en plano borrado**: Tras verificar OK, `soul_native.dump` se borra de forma segura (evita copias inseguras del alma en texto plano)
- **Recomendacion**: Activa **BitLocker** en la unidad para blindar el alma at-rest

## Solución de Problemas

| Error | Solución |
|-------|----------|
| _"Corré esto en PowerShell 'Ejecutar como administrador'"_ | Abre PowerShell > Click derecho > Ejecutar como administrador |
| _"Falta soul_native.dump"_ | Verifica que el dump esté en el mismo directorio que el .ps1 |
| _"Falta pgvector\vector.dll"_ | Compila pgvector desde v0.8.2 (nmake /F Makefile.win) o contacta |
| _"VERIFICACION FALLIDA"_ | El dump no restauró correctamente; re-corre el instalador |

## Versiones

- PostgreSQL: 17 (EDB, oficial)
- pgvector: v0.8.2 (compilado nativo MSVC)
- SOUL schema: v3 (176 tablas, 20 vistas, 105976+ memorias)

---

**Instalador creado por**: Team SEAL  
**Especificacion SOUL**: /home/dadito/IA/proyecto-seal/SOUL_EXACT_SPEC/
