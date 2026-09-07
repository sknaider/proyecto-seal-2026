"""Hook PreToolUse que NIEGA borrados con variable/glob (regla de oro William 9-ago/7-sep-2026).
Todas las rutas de estos tests son SEÑUELOS bajo /tmp o inexistentes; el hook nunca ejecuta nada."""
import importlib.util
import json
import pathlib
import subprocess
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
HOOK = RAIZ / "tools/seal_guard_rm_variable.py"
spec = importlib.util.spec_from_file_location("guard", HOOK)
guard = importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)


def _corre(command: str, tool="Bash") -> dict | None:
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_name": tool, "tool_input": {"command": command}}),
                       capture_output=True, text=True, timeout=10)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def test_rojo_rm_con_variable_se_niega_sin_prompt():
    for c in ['rm -rf "$T"', 'rm "$M"/*.json', 'rm -r ~/senuelo', 'rm -- "${DIR}/a.txt"', 'sudo rm -rf "$X"',
              'T=$(mktemp -d /tmp/seal-senuelo-XXXXXX); echo hola; rm -rf "$T"', 'rm -f $(ls /tmp/senuelo)']:
        out = _corre(c)
        assert out and out["hookSpecificOutput"]["permissionDecision"] == "deny", c


def test_rojo_rm_con_glob_literal_tambien_se_niega():
    assert _corre('rm -f /tmp/seal-senuelo/*.log')["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_verde_rm_con_ruta_literal_pasa():
    for c in ['rm -rf /tmp/seal-senuelo-abc123', 'rm -f /tmp/seal-senuelo/a.log b.txt', 'rmdir /tmp/seal-senuelo-vacio']:
        assert _corre(c) is None, c


def test_verde_la_forma_documentada_para_temporales_pasa():
    assert _corre('find "$DIR" -mindepth 1 -delete') is None
    assert _corre('find "$DIR" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +') is None


def test_verde_no_confunde_texto_ni_otros_comandos():
    for c in ['echo "no borres con rm -rf $X"', 'git rm --cached "$F"', 'grep -n "rm -rf \\"$T\\"" tools/x.sh', 'ls "$DIR"/*.json']:
        assert _corre(c) is None, c


def test_verde_otra_herramienta_no_se_toca():
    assert _corre('rm -rf "$T"', tool="Read") is None


def test_control_el_mensaje_dice_el_reemplazo_y_la_regla():
    r = _corre('rm -rf "$T"')["hookSpecificOutput"]["permissionDecisionReason"]
    assert "find \"$DIR\" -mindepth 1 -delete" in r and "regla de oro" in r and "$T" in r


def test_control_el_hook_esta_cableado_en_settings_para_bash():
    s = json.loads((RAIZ / ".claude/settings.json").read_text())
    reglas = [r for r in s["hooks"]["PreToolUse"] if r.get("matcher") == "Bash"]
    assert any("seal_guard_rm_variable.py" in h["command"] for r in reglas for h in r["hooks"])
