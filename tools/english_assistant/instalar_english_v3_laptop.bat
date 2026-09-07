@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title English Assistant v3 — Instalador SEAL

echo.
echo  =====================================================
echo    English Assistant v3 — Instalador para Laptop
echo    Modelo: gemma4:e4b   ^|   Ollama local
echo  =====================================================
echo.

:: ── 1. Verificar Python ─────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python no encontrado.
    echo  Instala Python 3.10+ desde https://python.org
    echo  Marca la opcion "Add Python to PATH" al instalar.
    pause
    exit /b 1
)
python --version

:: ── 2. Verificar que el script principal esta aqui ──────────────────
if not exist "%~dp0english_assistant_v3.py" (
    echo.
    echo  ERROR: No se encontro english_assistant_v3.py en esta carpeta.
    echo  Copia english_assistant_v3.py junto a este instalador.
    pause
    exit /b 1
)
echo  Script: english_assistant_v3.py OK

:: ── 3. Verificar Ollama ─────────────────────────────────────────────
echo.
echo  Verificando Ollama en localhost:11434...
curl -s -o nul -w "%%{http_code}" http://localhost:11434/api/tags 2>nul | findstr "200" >nul
if errorlevel 1 (
    echo  AVISO: Ollama no responde. Asegurate de que este corriendo.
    echo  Comando para iniciar: ollama serve
    echo.
) else (
    echo  Ollama: OK
)

:: ── 4. Instalar dependencias Python ─────────────────────────────────
echo.
echo  Instalando SpeechRecognition...
pip install SpeechRecognition --quiet --disable-pip-version-check

echo  Instalando PyAudioWPatch ^(audio loopback Windows^)...
pip install PyAudioWPatch --quiet --disable-pip-version-check
if errorlevel 1 (
    echo  Intentando PyAudio alternativo...
    pip install PyAudio --quiet --disable-pip-version-check
)

echo  Dependencias instaladas.

:: ── 5. Crear acceso directo en Escritorio ───────────────────────────
echo.
set "SCRIPTDIR=%~dp0"
set "SCRIPTDIR=%SCRIPTDIR:~0,-1%"
set "LAUNCHER=%USERPROFILE%\Desktop\English Assistant v3.bat"

(
    echo @echo off
    echo title English Assistant v3 - SEAL
    echo set OLLAMA_URL=http://localhost:11434
    echo set OLLAMA_MODEL=gemma4:e4b
    echo cd /d "%SCRIPTDIR%"
    echo python english_assistant_v3.py
    echo pause
) > "%LAUNCHER%"

echo  Acceso directo creado: %LAUNCHER%

:: ── 6. Lanzar ahora ─────────────────────────────────────────────────
echo.
echo  =====================================================
echo    INICIANDO English Assistant v3
echo    Ctrl = grabar audio del sistema (loopback)
echo    Suelta Ctrl = genera respuesta en ingles
echo    pronunciacion fonetica en espanol incluida
echo  =====================================================
echo.

set OLLAMA_URL=http://localhost:11434
set OLLAMA_MODEL=gemma4:e4b
cd /d "%~dp0"
python english_assistant_v3.py

if errorlevel 1 (
    echo.
    echo  Si hubo error, verifica:
    echo    1. Ollama corriendo: ollama serve
    echo    2. Modelo: ollama pull gemma4:e4b
    echo    3. PyAudioWPatch instalado
    echo    4. "Mezcla estereo" habilitada en sonido Windows
    pause
)
