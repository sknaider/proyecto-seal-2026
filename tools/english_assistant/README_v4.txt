==================================================================
  English Assistant v4 — Coach de Examen Oral de Inglés (SEAL)
==================================================================

QUÉ ES
  Te ayuda EN VIVO durante un examen oral de inglés:
  - Oye al examinador (audio del sistema, Windows WASAPI loopback)
    o le escribís lo que dijo.
  - Te da 3 respuestas en inglés + su PRONUNCIACIÓN en fonética
    española, en una ventana siempre-encima.
  - Predice 3 preguntas de seguimiento que el examinador podría hacer.
  - Tiene un apartado dedicado "CÓMO SE PRONUNCIA".

REQUISITOS (laptop Windows)
  1) Python 3.10+  (marcá "Add Python to PATH" al instalar)
  2) Dependencias:   pip install -r requirements.txt
  3) Ollama corriendo con el modelo:
        ollama serve
        ollama pull qwen2.5:7b
     (Necesita internet para la transcripción de voz — Google STT.)

CÓMO ABRIRLO
  - Doble clic en  LANZAR_v4.bat
  - o:   python english_assistant_v4.py

USO
  - Escribí la pregunta del examinador y Enter, o
  - Mantené CTRL para grabar el audio del sistema (suéltalo al terminar).
  - Botón "Generar Preguntas" = 8 preguntas de práctica.

NOTAS
  - El modo VOZ (WASAPI loopback) solo funciona en Windows.
  - Si Ollama falla, hay fallback a Anthropic (necesita ANTHROPIC_API_KEY).

ARCHIVOS
  english_assistant_v4.py   <- el principal (el que usás)
  english_overlay.py        <- overlay alternativo minimalista
  requirements.txt          <- dependencias
  LANZAR_v4.bat             <- launcher

Fixes recientes (SEAL):
  - Corregido un bug de threading en el camino de voz (FABLE).
  - Agregado el apartado dedicado de pronunciación (JARVIS).
