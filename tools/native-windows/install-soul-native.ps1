<#
============================================================================
 install-soul-native.ps1 - Instalador NATIVO INDUSTRIAL de SOUL para Windows.
 A PRUEBA DE TODO (pedido William 8-jul): preflight + integridad + reintentos
 + IDEMPOTENTE/RESUMIBLE (si se corta, re-correr detecta hasta donde llego y
 SIGUE, no empieza de cero) + AUTO-SANADOR + diagnostico claro.

 POR QUE NATIVO: WSL2 en VM VMware choca con Hyper-V (dead-end nested-virt).
 Nativo = Postgres 17 binarios + pgvector + tu alma. Corre en cualquier Windows.

 BUNDLE (junto a este .ps1): soul_native.dump, soul_roles.sql, soul_native.counts,
 carpeta pgvector\, y (self-contained) postgresql-*-binaries.zip.
 USO: doble-click install-soul-native.bat (auto-eleva). Re-correr es SEGURO.
============================================================================
#>
[CmdletBinding()]
param(
  [string]$PgVersion   = "17",
  [int]   $PgPort      = 5432,
  [string]$SuperPass   = "postgres",
  [string]$DbName      = "seal_memory",
  [string]$BundleDir   = $PSScriptRoot,
  [string]$EdbUrl      = "https://get.enterprisedb.com/postgresql/postgresql-17.6-1-windows-x64-binaries.zip"
)
$ErrorActionPreference = "Stop"

function Say ($m){ Write-Host "==> $m" -ForegroundColor Cyan }
function Ok  ($m){ Write-Host "    ok  $m" -ForegroundColor Green }
function Warn($m){ Write-Host "    !!  $m" -ForegroundColor Yellow }
function Die ($m){ Write-Host "XX  $m" -ForegroundColor Red; exit 1 }
function Retry([scriptblock]$Act,[string]$What="operacion",[int]$Tries=3,[int]$WaitSec=4){
  for($i=1;$i -le $Tries;$i++){
    try { return (& $Act) }
    catch { Warn "$What fallo (intento $i/$Tries): $($_.Exception.Message)"; if($i -lt $Tries){ Start-Sleep $WaitSec } }
  }
  Die "$What fallo tras $Tries intentos."
}

# --- BundleDir ROBUSTO (el default $PSScriptRoot puede quedar vacio segun invocacion) ---
if ([string]::IsNullOrWhiteSpace($BundleDir)) {
  $BundleDir = if     ($PSScriptRoot)                { $PSScriptRoot }
               elseif ($MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path }
               else                                  { (Get-Location).Path }
}
$PgRoot = "C:\Program Files\PostgreSQL\$PgVersion"
$PgBin  = Join-Path $PgRoot "bin"
$Data   = Join-Path $PgRoot "data"
$Svc    = "postgresql-soul-$PgVersion"
$psql   = Join-Path $PgBin "psql.exe"
$pgrest = Join-Path $PgBin "pg_restore.exe"
$env:PGPASSWORD = $SuperPass

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  SOUL - instalador NATIVO industrial (sin WSL, resumible)"   -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Say "bundle: $BundleDir"

# --- self-heal: asegurar que el servidor este ARRIBA antes de operar la DB ---
function Test-ServerUp {
  & $psql -U postgres -h localhost -p $PgPort -tAc "SELECT 1" 2>$null | Out-Null
  return ($LASTEXITCODE -eq 0)
}
function Ensure-Server {
  if (Test-ServerUp) { return }
  Warn "servidor no responde en :$PgPort - intentando arrancar..."
  Start-Service -Name $Svc -EA SilentlyContinue; Start-Sleep 3
  if (Test-ServerUp) { Ok "servidor arriba (servicio)"; return }
  if (Test-Path (Join-Path $PgBin "pg_ctl.exe")) {
    & (Join-Path $PgBin "pg_ctl.exe") -D $Data -o "-p $PgPort" -l (Join-Path $Data "startup.log") -w start 2>&1 | Out-Null
    Start-Sleep 2
  }
  if (-not (Test-ServerUp)) { Die "no pude arrancar PostgreSQL (revisa $Data\startup.log)." }
  Ok "servidor arriba (pg_ctl)"
}

# ================= PREFLIGHT (falla claro y TEMPRANO, no a mitad) =================
Say "[preflight] verificando prerequisitos y archivos del bundle..."
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
  Die "Necesita Administrador. Click derecho en install-soul-native.bat -> 'Ejecutar como administrador'."
}
if (-not [Environment]::Is64BitOperatingSystem) { Die "Requiere Windows 64-bit." }
try { $freeGB = [math]::Round((Get-PSDrive C -EA Stop).Free/1GB,1) } catch { $freeGB = 99 }
if ($freeGB -lt 4) { Die "Poco espacio libre en C: ${freeGB} GB (necesita ~4 GB)." }
foreach($f in @("soul_native.dump","soul_roles.sql","soul_native.counts")){
  if(-not (Test-Path (Join-Path $BundleDir $f))){ Die "Falta '$f' en el bundle ($BundleDir). Descomprimiste TODO el zip? Re-descargalo COMPLETO." }
}
if(-not (Test-Path (Join-Path $BundleDir "pgvector\vector.dll"))){ Die "Falta 'pgvector\vector.dll'. Descomprimiste TODO el zip?" }
# INTEGRIDAD: el dump debe pesar ~629 MB - atrapa descarga/extract incompleto (catch FABLE)
$dumpItem = Get-Item (Join-Path $BundleDir "soul_native.dump")
$dumpMB = [math]::Round($dumpItem.Length/1MB)
if ($dumpItem.Length -lt 600MB) { Die "soul_native.dump esta INCOMPLETO ($dumpMB MB, deberia ser ~629 MB). Tu descarga se corto - re-descargalo completo." }
Ok "preflight OK: admin, x64, ${freeGB} GB libres, archivos presentes (dump $dumpMB MB)."

# ================= [1/6] PostgreSQL 17 (idempotente + resumible + retry) =================
if (Test-Path $psql) { Ok "[1/6] PostgreSQL ya instalado (skip)." }
else {
  Say "[1/6] Instalando PostgreSQL $PgVersion (binarios oficiales)..."
  $localZip = Get-ChildItem -Path $BundleDir -Filter "postgresql-*binaries.zip" -EA SilentlyContinue | Select-Object -First 1
  if ($localZip) { $zip = $localZip.FullName; $lzMB = [math]::Round($localZip.Length/1MB); Ok "binarios locales: $($localZip.Name) ($lzMB MB)" }
  else {
    $zip = Join-Path $env:TEMP "pg-bin.zip"
    Retry { Say "    descargando binarios PG (~315 MB)..."; Invoke-WebRequest -Uri $EdbUrl -OutFile $zip -UseBasicParsing } "descarga de binarios PG"
  }
  $zipMB = [math]::Round((Get-Item $zip).Length/1MB)
  if ((Get-Item $zip).Length -lt 200MB) { Die "el zip de binarios esta incompleto ($zipMB MB). Re-intenta." }
  $ext = Join-Path $env:TEMP "pg-extract"
  Retry { Remove-Item $ext -Recurse -Force -EA SilentlyContinue; Say "    extrayendo binarios..."; Expand-Archive -Path $zip -DestinationPath $ext -Force } "extraccion de binarios"
  New-Item -ItemType Directory -Force -Path (Split-Path $PgRoot) | Out-Null
  if (Test-Path $PgRoot) { Remove-Item $PgRoot -Recurse -Force -EA SilentlyContinue }
  Move-Item -Path (Join-Path $ext "pgsql") -Destination $PgRoot -Force
  if (-not (Test-Path (Join-Path $PgBin "initdb.exe"))) { Die "binarios PG incompletos (falta initdb.exe)." }
  Ok "binarios PG colocados."
  if (-not (Test-Path (Join-Path $Data "PG_VERSION"))) {
    $pwf = Join-Path $env:TEMP "pgpw.txt"; Set-Content -Path $pwf -Value $SuperPass -NoNewline -Encoding ascii
    & (Join-Path $PgBin "initdb.exe") -D $Data -U postgres --pwfile="$pwf" -A scram-sha-256 -E UTF8 2>&1 | Out-Null
    Remove-Item $pwf -Force -EA SilentlyContinue
    if (-not (Test-Path (Join-Path $Data "PG_VERSION"))) { Die "initdb fallo (no se creo el cluster)." }
    Add-Content -Path (Join-Path $Data "postgresql.conf") -Value "`nport = $PgPort`nlisten_addresses = 'localhost'"
    Ok "cluster inicializado."
  }
  if (-not (Get-Service $Svc -EA SilentlyContinue)) {
    & (Join-Path $PgBin "pg_ctl.exe") register -N $Svc -D $Data -S auto 2>&1 | Out-Null
  }
  Ok "[1/6] PostgreSQL $PgVersion instalado."
}
# De aqui en adelante manejamos herramientas NATIVAS (psql/pg_ctl/pg_restore) que escriben
# avisos y "connection refused" a stderr de forma NORMAL. Con 'Stop' eso se vuelve error
# terminante y mata el auto-sanador y el restore. Pasamos a 'Continue': la validacion es por
# EFECTO (Test-Path, $LASTEXITCODE, y el scorecard fail-hard vs manifest), no por excepciones.
# (Retry{} -que necesita 'Stop'- solo se usa en [1/6], mas arriba.)
$ErrorActionPreference = "Continue"

Ensure-Server   # auto-sanador: garantiza server arriba (aunque PG ya estuviera de una corrida previa)

# ================= [2/6] pgvector (idempotente) =================
if (Test-Path (Join-Path $PgRoot "lib\vector.dll")) { Ok "[2/6] pgvector ya colocado (skip)." }
else {
  Say "[2/6] Instalando pgvector (archivos nativos)..."
  $pgvDir = Join-Path $BundleDir "pgvector"
  Copy-Item (Join-Path $pgvDir "vector.dll")     (Join-Path $PgRoot "lib\vector.dll") -Force
  Copy-Item (Join-Path $pgvDir "vector.control") (Join-Path $PgRoot "share\extension\") -Force
  Copy-Item (Join-Path $pgvDir "vector--*.sql")  (Join-Path $PgRoot "share\extension\") -Force
  Ok "[2/6] pgvector colocado (lib + share\extension)."
}

# ================= [3/6] roles + base (idempotente) =================
Say "[3/6] Roles y base $DbName..."
& $psql -U postgres -h localhost -p $PgPort -v ON_ERROR_STOP=0 -f (Join-Path $BundleDir "soul_roles.sql") 2>&1 |
  Where-Object { $_ -notmatch "already exists|ya existe" } | ForEach-Object { Write-Host "    $_" }
$dbExists = "$(& $psql -U postgres -h localhost -p $PgPort -tAc "SELECT 1 FROM pg_database WHERE datname='$DbName'")".Trim()
if ($dbExists -ne "1") { & $psql -U postgres -h localhost -p $PgPort -c "CREATE DATABASE $DbName OWNER seal" | Out-Null; Ok "base $DbName creada." }
else { Ok "base $DbName ya existe (skip)." }
& $psql -U postgres -h localhost -p $PgPort -c "ALTER DATABASE $DbName SET max_parallel_maintenance_workers=0;" 2>&1 | Out-Null

# manifest (para el skip-de-restore y la verificacion)
$manifestPath = Join-Path $BundleDir "soul_native.counts"
if (-not (Test-Path $manifestPath)) { Die "Falta soul_native.counts (manifest) en el bundle." }
$M = @{}
Get-Content $manifestPath | ForEach-Object { if ($_ -match '^\s*([^=]+)=(\d+)\s*$') { $M[$matches[1].Trim()] = [int]$matches[2] } }

# ================= [4/6] restaurar el alma (idempotente/RESUMIBLE) =================
$curMem = "$(& $psql -U postgres -h localhost -p $PgPort -d $DbName -tAc "SELECT count(*) FROM soul_v3.memories" 2>$null)".Trim()
if ($curMem -match '^\d+$' -and [int]$curMem -ge [int]$M['memories']) {
  Ok "[4/6] alma ya restaurada ($curMem memorias) (skip)."
} else {
  Say "[4/6] Restaurando el alma (105k+ memorias, puede tardar unos minutos)..."
  $dump = Join-Path $BundleDir "soul_native.dump"
  & $pgrest -U postgres -h localhost -p $PgPort -d $DbName -j 2 $dump 2>&1 |
    Where-Object { $_ -match "error|ERROR|fatal|FATAL" -and $_ -notmatch "already exists|ya existe" } |
    ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
  Ok "[4/6] restore terminado."
}

# ================= [5/6] verificacion fail-hard vs MANIFEST (scorecard completo) =================
Say "[5/6] Verificando completitud (exacto vs manifest del dump)..."
$q = @"
SELECT
 (SELECT count(*) FROM information_schema.tables WHERE table_schema='soul_v3' AND table_type='BASE TABLE'),
 (SELECT count(*) FROM information_schema.views WHERE table_schema='soul_v3'),
 (SELECT count(*) FROM pg_indexes WHERE schemaname='soul_v3'),
 (SELECT count(*) FROM pg_indexes WHERE schemaname='soul_v3' AND (indexdef ILIKE '%hnsw%' OR indexdef ILIKE '%ivfflat%')),
 (SELECT count(*) FROM pg_policies WHERE schemaname='soul_v3'),
 (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid WHERE n.nspname='soul_v3' AND c.relforcerowsecurity),
 (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid WHERE n.nspname='soul_v3'),
 (SELECT count(*) FROM information_schema.sequences WHERE sequence_schema='soul_v3'),
 (SELECT count(*) FROM pg_extension WHERE extname IN ('vector','pg_trgm','pgcrypto')),
 (SELECT count(*) FROM soul_v3.memories);
"@
$r = ("$(& $psql -U postgres -h localhost -p $PgPort -d $DbName -tAF"|" -c $q)").Trim()
$p = $r -split '\|'
$got = @{ tables_soul_v3=[int]$p[0]; views=[int]$p[1]; indexes=[int]$p[2]; idx_vector=[int]$p[3];
          policies=[int]$p[4]; rls_forced=[int]$p[5]; functions=[int]$p[6]; sequences=[int]$p[7];
          ext_key=[int]$p[8]; memories=[int]$p[9] }
Write-Host "    SCORECARD (restaurado vs dump):"
$fail = @()
foreach ($k in @('tables_soul_v3','views','indexes','idx_vector','policies','rls_forced','functions','sequences','ext_key','memories')) {
  if (-not $M.ContainsKey($k)) { continue }
  $isOk = ($got[$k] -eq $M[$k])
  Write-Host ("      {0,-16} {1,7}  (dump {2})  {3}" -f $k, $got[$k], $M[$k], $(if($isOk){'OK'}else{'<-- MISMATCH'}))
  if (-not $isOk) { $fail += $k }
}
$tabs = $got['tables_soul_v3']; $mems = $got['memories']
if ($fail.Count -gt 0) {
  Die ("VERIFICACION FALLIDA (mismatch: {0}). Restore incompleto o INSEGURO. NO declaro instalado. Re-corre el instalador: es idempotente y RESUME donde quedo." -f ($fail -join ', '))
}
Ok "[5/6] scorecard completo: todo cuadra con el manifest."

# ================= [6/6] seguridad: localhost-only + wipe del dump en plano =================
Say "[6/6] Endureciendo: localhost-only + borrando el dump en plano..."
$hba = Join-Path $Data "pg_hba.conf"
if (Test-Path $hba) {
  (Get-Content $hba) | Where-Object { $_ -notmatch "0\.0\.0\.0/0" -and $_ -notmatch "::/0" } | Set-Content $hba
}
& $psql -U postgres -h localhost -p $PgPort -c "ALTER SYSTEM SET listen_addresses='localhost';" 2>&1 | Out-Null
Restart-Service -Name $Svc -Force -EA SilentlyContinue
Remove-Item (Join-Path $BundleDir "soul_native.dump") -Force -EA SilentlyContinue

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  SOUL NATIVO INSTALADO Y VERIFICADO (sin WSL)." -ForegroundColor Green
Write-Host ("  soul_v3: {0} tablas | {1} memorias | Postgres {2} en localhost:{3}" -f $tabs,$mems,$PgVersion,$PgPort) -ForegroundColor Green
Write-Host "  RECOMENDACION: activa BitLocker para blindar el alma at-rest." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
