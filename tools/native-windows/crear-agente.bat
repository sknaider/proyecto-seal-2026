@echo off
REM ============================================================================
REM crear-agente.bat - PROTOTIPO del creador/personalizador de agente SOUL.
REM Doble-click: instala deps (Python) y lanza el wizard para crear tu agente
REM sobre el alma local del device. Pedido de William (prototipo para probar).
REM ============================================================================
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Creador de Agente SOUL - PROTOTIPO
echo ============================================================

REM 1) Python presente?
where python >nul 2>&1
if %errorlevel% neq 0 (
  echo.
  echo [X] No encuentro Python. Instalalo desde https://www.python.org/downloads/windows/
  echo     (marca "Add python.exe to PATH" al instalar) y vuelve a correr este .bat.
  echo.
  pause
  exit /b 1
)

REM 2) deps (asyncpg + mcp). Idempotente: si ya estan, pip no re-descarga.
echo.
echo Instalando dependencias (asyncpg, mcp)...
python -m pip install --quiet --disable-pip-version-check asyncpg mcp
if %errorlevel% neq 0 (
  echo [X] Fallo instalando dependencias. Revisa tu conexion e intenta de nuevo.
  pause
  exit /b 1
)

REM 3) lanzar el wizard (usa el Postgres local por defecto: localhost:5432/seal_memory)
echo.
echo Lanzando el wizard de creacion de agente...
echo.
python create_agent.py
echo.
echo ============================================================
echo   Listo. Si creo el agente, se genero un .mcp.json en esta carpeta.
echo   Copialo a la carpeta donde corres 'claude' y arranca Claude ahi.
echo ============================================================
pause
endlocal
