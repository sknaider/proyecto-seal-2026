"""Mutación conductual del rewrite Bob→SOUL, siempre sobre copias temporales."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
TARGETS = [ROOT / "memory/agent_loop_guard.py", ROOT / "memory/compaction_observer.py"]
TEST = "tests/test_bob_rewrite_runtime.py"

MUTANTS = [
    ("loop_stop_threshold", ROOT / "memory/agent_loop_guard.py", "if count >= self.stop_after:", "if count > self.stop_after:"),
    ("loop_progress_reset", ROOT / "memory/agent_loop_guard.py", "if previous == key", "if previous != key"),
    ("compaction_type_guard", ROOT / "memory/compaction_observer.py", "if not isinstance(result, list):", "if isinstance(result, list):"),
    ("compaction_passthrough", ROOT / "memory/compaction_observer.py", "result = list(messages)", "result = []"),
]


def run() -> dict:
    results = []
    control = subprocess.run(["python3", "-m", "pytest", "-q", TEST], cwd=ROOT, capture_output=True, text=True)
    if control.returncode != 0:
        raise SystemExit("control rojo:\n" + control.stdout + control.stderr)
    for name, source, old, new in MUTANTS:
        with tempfile.TemporaryDirectory(prefix="soul-bob-mutant-") as tmp:
            sandbox = Path(tmp) / "repo"
            (sandbox / "memory").mkdir(parents=True)
            (sandbox / "tests").mkdir(parents=True)
            for src in TARGETS:
                shutil.copy2(src, sandbox / src.relative_to(ROOT))
            shutil.copy2(ROOT / TEST, sandbox / TEST)
            target = sandbox / source.relative_to(ROOT)
            text = target.read_text(encoding="utf-8")
            if old not in text:
                raise SystemExit(f"mutante no aplicado: {name}")
            target.write_text(text.replace(old, new, 1), encoding="utf-8")
            proc = subprocess.run(["python3", "-m", "pytest", "-q", TEST], cwd=sandbox, capture_output=True, text=True)
            results.append({"name": name, "killed": proc.returncode != 0, "returncode": proc.returncode, "output": (proc.stdout + proc.stderr)[-500:]})
    return {"control": "passed", "total": len(results), "killed": sum(r["killed"] for r in results), "survived": sum(not r["killed"] for r in results), "results": results}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
