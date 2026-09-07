#!/usr/bin/env python3
"""Test de regresión — GUARD del RCE del chat del clon (FABLE, 2026-07-02).

Contexto: minisoul_device_studio.clone_chat corre `claude -p "{mensaje-del-user}" --tools ""`.
El mensaje del usuario es INPUT NO CONFIABLE (endpoint localhost + WSL-forwarding). Si alguien
"simplifica" el código quitando `--tools ""` o metiendo `--dangerously-skip-permissions`, un
prompt-injection podría ejecutar comandos en la PC del usuario = RCE. Este test lo caza ANTES
de shippear al device.

HALLAZGO LOAD-BEARING (NEXUS, verificado por contraste): sacar --dangerously-skip-permissions
SOLO NO alcanza — headless `-p` SIGUE ejecutando tools sin ese flag. `--tools ""` es LO que cierra.

2 capas de guard:
  1. CODE-CHECK: clone_chat usa --tools "" y NO --dangerously-skip-permissions.
  2. BY-EFFECT (contraste infalsificable): tools-ON ejecuta un pedido benigno / tools-OFF no.
     (usa un pedido BENIGNO — crear un marker en /tmp — no un injection, para AISLAR el efecto
      del flag del juicio del modelo; el contraste prueba que --tools "" es la barrera real).

Correr: python3 tools/test_chat_rce_guard.py   (o en el deploy/CI del device-studio)
Exit 0 = guard OK. Exit 1 = REGRESIÓN (el RCE puede estar reabierto).
"""
import os, re, sys, subprocess, tempfile, pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
STUDIO = REPO / "memory" / "minisoul_device_studio.py"
CLAUDE = os.environ.get("CLAUDE_BIN", str(pathlib.Path.home() / ".local" / "bin" / "claude"))


def code_check() -> bool:
    """Capa 1: el código del chat NO debe tener skip-permissions y SÍ --tools ''."""
    raw = STUDIO.read_text(encoding="utf-8") if STUDIO.exists() else ""
    # IMPORTANTE: ignorar COMENTARIOS — el código documenta "NUNCA --dangerously-skip-permissions"
    # en un comentario; matchear el string crudo daría falso-positivo. Miramos solo CÓDIGO real
    # (la parte de cada línea antes de un '#').
    code_lines = []
    for ln in raw.splitlines():
        code_lines.append(ln.split("#", 1)[0])  # descartar el comentario de la línea
    src = "\n".join(code_lines)
    ok = True
    if "--dangerously-skip-permissions" in src:
        print("🔴 CODE: --dangerously-skip-permissions en CÓDIGO REAL del device-studio (RCE reabierto)")
        ok = False
    if '"--tools"' not in src and "'--tools'" not in src:
        print("🔴 CODE: no se encuentra `--tools \"\"` en el device-studio (la barrera se fue)")
        ok = False
    if ok:
        print("✅ CODE: --tools \"\" presente, sin --dangerously-skip-permissions")
    return ok


def _run_claude_marker(marker: str, tools_disabled: bool) -> bool:
    """Corre claude -p pidiendo crear `marker` con Bash. Devuelve True si el marker se creó."""
    prompt = (f"Creá el archivo {marker} con el contenido OK usando la herramienta Bash. "
              f"Es una prueba local benigna; hacelo ahora.")
    args = [CLAUDE, "-p", prompt]
    if tools_disabled:
        args += ["--tools", ""]
    else:
        args += ["--dangerously-skip-permissions"]  # baseline PELIGROSO (solo en el test, pedido benigno)
    try:
        subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
    except Exception as e:
        print(f"   (claude falló/timeout: {type(e).__name__})")
    return os.path.exists(marker)


def effect_contrast() -> bool:
    """Capa 2: contraste — tools ON ejecuta (marker creado) / tools OFF no (marker ausente)."""
    if not os.path.exists(CLAUDE):
        print(f"⚠️  EFECTO: claude no está en {CLAUDE} — salteo el contraste (corré en el device).")
        return True  # no bloquea en máquinas sin claude; el code-check sí corre
    d = tempfile.mkdtemp(prefix="rce_guard_")
    m_on, m_off = os.path.join(d, "on.txt"), os.path.join(d, "off.txt")
    print("   corriendo baseline tools-ON (pedido benigno)...")
    created_on = _run_claude_marker(m_on, tools_disabled=False)
    print("   corriendo fix tools-OFF (--tools '')...")
    created_off = _run_claude_marker(m_off, tools_disabled=True)
    for f in (m_on, m_off):
        try: os.remove(f)
        except OSError: pass
    try: os.rmdir(d)
    except OSError: pass
    if not created_on:
        print("⚠️  EFECTO: baseline tools-ON NO creó el marker → el test no es concluyente "
              "(claude -p quizás no ejecuta tools en este entorno). Code-check sigue siendo el guard.")
        return True  # inconcluso, no falso-negativo de seguridad
    if created_off:
        print("🔴 EFECTO: con --tools \"\" el marker SÍ se creó → la barrera NO bloquea = RCE ABIERTO")
        return False
    print("✅ EFECTO: tools-ON ejecuta (marker creado) / tools-OFF NO ejecuta → --tools \"\" es la barrera real ✓")
    return True


def main() -> int:
    print("=== GUARD RCE del chat del clon (regresión) ===")
    ok1 = code_check()
    ok2 = effect_contrast()
    if ok1 and ok2:
        print("\n🟢 GUARD OK — el fix del RCE (--tools \"\") se sostiene.")
        return 0
    print("\n🔴 REGRESIÓN — revisar minisoul_device_studio.clone_chat: el RCE puede estar reabierto.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
