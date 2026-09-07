@echo off
title English Assistant v3 — Spark

pip install SpeechRecognition --quiet --disable-pip-version-check
pip install PyAudioWPatch --quiet --disable-pip-version-check
if errorlevel 1 pip install PyAudio --quiet --disable-pip-version-check

set OLLAMA_URL=http://100.75.201.110:11435
set OLLAMA_MODEL=gemma4-r2:latest

cd /d "%~dp0"
python english_assistant_v3.py
pause
