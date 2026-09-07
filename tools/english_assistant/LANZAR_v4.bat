@echo off
chcp 65001 >nul 2>&1
title English Assistant v4 - SEAL
cd /d "%~dp0"

echo ============================================
echo   English Assistant v4 - Coach de Examen Oral
echo ============================================
echo.

REM Modelo local (Ollama). Cambia aqui si usas otro.
set OLLAMA_URL=http://localhost:11434
set OLLAMA_MODEL=qwen2.5:7b

python english_assistant_v4.py
if errorlevel 1 (
  echo.
  echo  [ERROR] No arranco. Verifica:
  echo    1) Python instalado y en PATH
  echo    2) pip install -r requirements.txt
  echo    3) Ollama corriendo: ollama serve  ^&  ollama pull qwen2.5:7b
  pause
)
