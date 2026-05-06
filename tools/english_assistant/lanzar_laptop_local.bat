@echo off
echo ================================================
echo   English Assistant v3 — Ollama LOCAL
echo   Modelo: gemma4:e4b   Puerto: localhost:11434
echo ================================================

:: Dependencias
pip install SpeechRecognition --quiet
pip install PyAudioWPatch --quiet
if errorlevel 1 (
    pip install PyAudio --quiet
)

:: Apuntar a Ollama local
set OLLAMA_URL=http://localhost:11434
set OLLAMA_MODEL=gemma4:e4b

echo.
echo Modelo: %OLLAMA_MODEL%
echo Ollama: %OLLAMA_URL%
echo.

:: Verificar que Ollama corre
curl -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo ADVERTENCIA: Ollama no responde en localhost:11434
    echo Asegurate de que Ollama este corriendo antes de continuar.
    pause
)

echo Iniciando...
python english_assistant_v4.py
pause
