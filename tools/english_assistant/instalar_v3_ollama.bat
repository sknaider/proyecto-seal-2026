@echo off
setlocal enabledelayedexpansion
title English Assistant v4 — Instalador Ollama

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║     English Assistant v4 — Instalador Ollama         ║
echo  ║     Team SEAL                                         ║
echo  ╚══════════════════════════════════════════════════════╝
echo.

:: ── Verificar Python ──────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python no encontrado. Instala Python 3.10+ desde python.org
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version 2^>^&1') do echo [OK] %%i

:: ── Instalar dependencias ─────────────────────────────────────────────
echo.
echo [...] Instalando dependencias...
pip install SpeechRecognition --quiet --disable-pip-version-check
echo [OK] SpeechRecognition

pip install PyAudioWPatch --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [!] PyAudioWPatch fallo, intentando PyAudio...
    pip install PyAudio --quiet --disable-pip-version-check
    if errorlevel 1 (
        echo [WARN] PyAudio no se pudo instalar automaticamente.
        echo        Descarga el wheel desde: https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio
    ) else (
        echo [OK] PyAudio instalado
    )
) else (
    echo [OK] PyAudioWPatch ^(WASAPI loopback^)
)

:: ── Configurar Ollama ─────────────────────────────────────────────────
echo.
echo ──────────────────────────────────────────
echo  CONFIGURACION OLLAMA
echo ──────────────────────────────────────────
set OLLAMA_URL_DEFAULT=http://localhost:11434
set OLLAMA_MODEL_DEFAULT=gemma4:e4b

set /p OLLAMA_URL_INPUT="URL Ollama [Enter = %OLLAMA_URL_DEFAULT%]: "
if "!OLLAMA_URL_INPUT!"=="" (
    set OLLAMA_URL=!OLLAMA_URL_DEFAULT!
) else (
    set OLLAMA_URL=!OLLAMA_URL_INPUT!
)

set /p OLLAMA_MODEL_INPUT="Modelo Ollama [Enter = %OLLAMA_MODEL_DEFAULT%]: "
if "!OLLAMA_MODEL_INPUT!"=="" (
    set OLLAMA_MODEL=!OLLAMA_MODEL_DEFAULT!
) else (
    set OLLAMA_MODEL=!OLLAMA_MODEL_INPUT!
)

echo.
echo [CONFIG] URL   : !OLLAMA_URL!
echo [CONFIG] Modelo: !OLLAMA_MODEL!

:: ── Verificar Ollama ──────────────────────────────────────────────────
echo.
echo [...] Verificando conexion a Ollama...
curl -s --max-time 5 "!OLLAMA_URL!/api/tags" >nul 2>&1
if errorlevel 1 (
    echo [WARN] Ollama no responde en !OLLAMA_URL!
    echo        Asegurate de que Ollama este corriendo: ollama serve
    echo.
    set /p CONTINUE_ANYWAY="Continuar de todas formas? [S/N]: "
    if /i "!CONTINUE_ANYWAY!" neq "S" exit /b 0
) else (
    echo [OK] Ollama responde
    :: Verificar si el modelo existe
    curl -s --max-time 5 "!OLLAMA_URL!/api/tags" 2>nul | python -c "import sys,json; d=json.load(sys.stdin); models=[m['name'] for m in d.get('models',[])]; print('[OK] Modelo encontrado: !OLLAMA_MODEL!' if any('!OLLAMA_MODEL!' in m for m in models) else '[WARN] !OLLAMA_MODEL! no encontrado. Modelos disponibles: '+', '.join(models[:5]))" 2>nul
)

:: ── Crear lanzador configurado ────────────────────────────────────────
echo.
echo [...] Creando lanzador correr_english_v3.bat...
(
    echo @echo off
    echo set OLLAMA_URL=!OLLAMA_URL!
    echo set OLLAMA_MODEL=!OLLAMA_MODEL!
    echo echo Iniciando English Assistant v4 con !OLLAMA_MODEL!...
    echo cd /d "%%~dp0"
    echo python english_assistant_v4.py
    echo pause
) > "%~dp0correr_english_v3.bat"
echo [OK] Lanzador creado: correr_english_v3.bat

:: ── Crear acceso directo en el Escritorio ────────────────────────────
echo [...] Creando acceso directo en el Escritorio...
set "APP_DIR=%~dp0"
set "LAUNCHER_PATH=%~dp0correr_english_v3.bat"
set "SHORTCUT=%USERPROFILE%\Desktop\English Assistant v4.lnk"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT%'); $s.TargetPath = '%LAUNCHER_PATH%'; $s.WorkingDirectory = '%APP_DIR%'; $s.Description = 'English Assistant v4 - SEAL'; $s.IconLocation = 'shell32.dll,3'; $s.Save()" >nul 2>&1
if exist "%SHORTCUT%" (
    echo [OK] Acceso directo ".lnk" creado en Escritorio
) else (
    copy "%LAUNCHER_PATH%" "%USERPROFILE%\Desktop\English Assistant v4.bat" >nul 2>&1
    echo [OK] Atajo .bat creado en Escritorio: English Assistant v4.bat
)

:: ── Lanzar ahora ──────────────────────────────────────────────────────
echo.
echo ══════════════════════════════════════════
echo  Instalacion completa.
echo  Doble-click en "English Assistant v4" del Escritorio.
echo ══════════════════════════════════════════
echo.
set /p LAUNCH_NOW="Iniciar English Assistant ahora? [S/N]: "
if /i "!LAUNCH_NOW!"=="S" (
    cd /d "%~dp0"
    python english_assistant_v4.py
)
pause
