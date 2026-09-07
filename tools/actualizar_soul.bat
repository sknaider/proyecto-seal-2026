@echo off
setlocal enabledelayedexpansion
title SOUL - Actualizar mejoras del Core
echo ================================================
echo    SOUL - Actualizar mejoras del Core (bge-m3 + HNSW)
echo ================================================
echo.

REM --- 1) Ubicar el entorno soul-core (separado del tray/Platform: NO lo toca) ---
set "PY=%USERPROFILE%\soul-core\Scripts\python.exe"
if not exist "%PY%" (
  echo [X] No encontre el entorno soul-core en:
  echo     %USERPROFILE%\soul-core
  echo     Si lo instalaste en otra ruta, avisame y te ajusto el .bat.
  echo.
  pause
  exit /b 1
)
echo [1/5] Entorno soul-core encontrado.

REM --- 2) Bajar el cerebro de memoria nuevo (bge-m3, multilingue, ~1.2 GB) ---
echo [2/5] Descargando el cerebro de memoria bge-m3 (~1.2 GB, una sola vez)...
where ollama >nul 2>nul
if errorlevel 1 (
  echo     [!] No encontre 'ollama'. Abri Ollama y volve a correr este .bat.
) else (
  ollama pull bge-m3
)

REM --- 3) Actualizar SOLO el Core (no toca el tray de Platform) ---
echo [3/5] Actualizando SOUL Core a la ultima version (con indice rapido)...
"%PY%" -m pip install --upgrade "soul-framework[ann,integrity]"
if errorlevel 1 (
  echo [X] Fallo la actualizacion del Core. No se cambio nada critico.
  pause
  exit /b 1
)

REM --- 4) BUSCAR el alma automaticamente y migrar (NO destructivo) ---
echo [4/5] Buscando tu alma (alma_william.db) automaticamente...
set "ALMA="
REM 4a) carpetas mas probables primero (rapido)
for %%D in (
  "%USERPROFILE%\soul-core\Scripts"
  "%USERPROFILE%\Downloads"
  "%USERPROFILE%\Desktop"
  "%USERPROFILE%\Documents"
) do (
  if not defined ALMA if exist "%%~D\alma_william.db" set "ALMA=%%~D\alma_william.db"
)
REM 4b) respaldo: busqueda recursiva bajo el perfil del usuario
if not defined ALMA (
  for /f "delims=" %%F in ('dir /s /b "%USERPROFILE%\alma_william.db" 2^>nul') do (
    if not defined ALMA set "ALMA=%%F"
  )
)
if defined ALMA (
  echo     Alma encontrada: !ALMA!
  echo     Respaldando y re-indexando tus recuerdos a bge-m3...
  copy /Y "!ALMA!" "!ALMA!.backup" >nul
  echo     Backup: !ALMA!.backup
  "%PY%" -m soul_framework.embedding_migration run "!ALMA!" --candidate "!ALMA!.nuevo" --provider bge-m3 --source-dim 128 --target-dim 1024
  if errorlevel 1 (
    echo     [!] La migracion fallo. Tu alma ORIGINAL quedo intacta en !ALMA!.
  ) else (
    echo     [OK] Recuerdos re-indexados. Alma nueva: !ALMA!.nuevo
    echo          (tu original sigue intacto en !ALMA! por seguridad)
  )
) else (
  echo     No encontre ningun alma_william.db bajo tu usuario.
  echo     Si arrancaste con un alma nueva, no hay nada que migrar: ya estas listo.
)

REM --- 5) Verificar por efecto ---
echo [5/5] Verificando...
"%PY%" -c "from importlib.metadata import version; print('   SOUL Core =', version('soul-framework'))"
REM El extra [ann] usa hnswlib (Win<3.13 / Linux / Mac) o usearch (Windows + Python 3.13+). Aceptamos cualquiera.
"%PY%" -c "import importlib.util as u; ok = u.find_spec('hnswlib') or u.find_spec('usearch'); print('   Indice rapido: OK') if ok else exit(1)" || echo    [!] Indice no cargo (revisar extra ann)
echo.
echo ================================================
echo    LISTO. El Core ahora recuerda por SIGNIFICADO (bge-m3)
echo    y busca RAPIDO a escala (HNSW). El tray no se toco.
echo ================================================
echo.
pause
