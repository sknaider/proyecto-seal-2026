@echo off
echo ================================================
echo   English Assistant — Team SEAL
echo   Instalando dependencias...
echo ================================================

:: Verificar Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no encontrado. Instala Python 3.10+ primero.
    pause
    exit /b 1
)

:: Instalar dependencias
pip install anthropic SpeechRecognition --quiet

:: PyAudio (a veces necesita wheel especial en Windows)
pip install PyAudio --quiet
if errorlevel 1 (
    echo Intentando instalar PyAudio via pipwin...
    pip install pipwin --quiet
    pipwin install pyaudio
)

:: Verificar ANTHROPIC_API_KEY
if "%ANTHROPIC_API_KEY%"=="" (
    echo.
    echo ATENCION: Variable ANTHROPIC_API_KEY no detectada.
    echo Configura tu API key antes de continuar:
    echo   set ANTHROPIC_API_KEY=sk-ant-...
    echo.
    set /p ANTHROPIC_API_KEY="Pega tu API key aqui: "
    setx ANTHROPIC_API_KEY "%ANTHROPIC_API_KEY%" >nul
)

echo.
echo ================================================
echo   Lanzando English Assistant...
echo ================================================
python english_assistant.py
pause
