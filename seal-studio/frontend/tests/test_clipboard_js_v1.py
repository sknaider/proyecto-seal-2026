"""Gate-compatible pytest wrapper for clipboard.ts Node.js test suite.
Each test invokes the JS suite via subprocess so the gate sees pytest arms.
"""
import subprocess
import sys
from pathlib import Path

FRONT = Path(__file__).parent.parent
NODE_CMD = ["/usr/bin/node", "--experimental-strip-types", "--test"]


def _run(pattern: str | None = None) -> subprocess.CompletedProcess:
    cmd = NODE_CMD.copy()
    if pattern:
        cmd += ["--test-name-pattern", pattern]
    cmd += ["tests/clipboard.test.mjs"]
    return subprocess.run(cmd, cwd=FRONT, capture_output=True, text=True, timeout=120)


def test_unit_suite_completa():
    r = _run()
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-500:]


def test_qa_positive_items_win():
    r = _run("clipboard items win")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_positive_files_fallback():
    r = _run("files fallback")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_positive_items_path_no_files():
    r = _run("items path returns result")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_negative_text_y_null_filtrados():
    r = _run("text, non-images")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_negative_string_kind_no_retorna():
    r = _run("string-kind item with image mime")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_negative_video_mime_no_retorna():
    r = _run("file-kind item with non-image mime")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_control_suite_no_falla_con_datos_vacios():
    """Control: la función no lanza con items=[] files=[] — devuelve [] sin error."""
    r = subprocess.run(
        ["/usr/bin/node", "--experimental-strip-types", "--input-type=module"],
        input=(
            "import { pastedImages } from './src/lib/clipboard.ts';\n"
            "const r = pastedImages({items: [], files: []});\n"
            "if (!Array.isArray(r) || r.length !== 0) { console.error('FAIL', r); process.exit(1); }\n"
        ),
        cwd=FRONT, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stderr[-500:]
