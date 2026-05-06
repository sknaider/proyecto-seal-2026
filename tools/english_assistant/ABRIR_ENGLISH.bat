@echo off
cd /d "%~dp0"
set OLLAMA_URL=http://localhost:11434
set OLLAMA_MODEL=qwen2.5:7b
echo Iniciando English Assistant v4 con qwen2.5:7b...
python english_assistant_v4.py
pause
