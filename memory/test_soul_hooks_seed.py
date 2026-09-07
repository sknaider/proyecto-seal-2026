#!/usr/bin/env python3
"""
test_soul_hooks_seed.py — F1 importador de seed. Usa un fixture sintético (no el settings vivo)
para ser reproducible en CI. Prueba lo que importa: mapear bien Y no tragar lo no mapeado.
"""
import json
import sys
import tempfile
from pathlib import Path

from soul_hooks_seed import derive_seed, coverage_report, _extract_scripts


def _fixture(tmp: Path) -> Path:
    settings = {
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"command": "python3 /x/active_recall_hook.py --agent JARVIS"}]},
            ],
            "Stop": [
                {"hooks": [
                    {"command": "python3 /x/memory_extraction_hook.py"},
                    {"command": "bash /x/autodream_8gates.sh"},
                ]},
            ],
            "FileChanged": [
                {"matcher": "jarvis_wakeup.jsonl",
                 "hooks": [{"command": "python3 /x/file_changed_context_hook.py"}]},
                {"matcher": "nexus_inbox.jsonl",
                 "hooks": [{"command": "python3 /x/file_changed_context_hook.py"}]},
            ],
            "EventoInventadoQueNoMapea": [
                {"hooks": [{"command": "python3 /x/misterio.py"}]},
            ],
        }
    }
    p = tmp / "settings.json"
    p.write_text(json.dumps(settings))
    return p


def main() -> int:
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(f"[{'PASS' if cond else 'FAIL'}] {name}")
        fails += 0 if cond else 1

    # _extract_scripts saca la RUTA ejecutable, no el basename.
    check("_extract_scripts saca ruta completa",
          _extract_scripts("python3 /x/active_recall_hook.py --a b") == ["/x/active_recall_hook.py"])

    with tempfile.TemporaryDirectory() as d:
        path = _fixture(Path(d))
        rows, unmapped = derive_seed(path)

        # 1. El evento no mapeado se REPORTA, no se descarta en silencio.
        check("evento sin mapear se reporta (no se traga)",
              unmapped == ["EventoInventadoQueNoMapea"])

        # 2. No se generó ninguna fila para el evento no mapeado.
        check("cero filas para el evento no mapeado",
              all("misterio.py" not in r.script_path for r in rows))

        # 3. UserPromptSubmit → on_prompt, clasificado learning.
        prompt_rows = [r for r in rows if r.script_path.endswith("active_recall_hook.py")]
        check("UserPromptSubmit→on_prompt kind=learning",
              len(prompt_rows) == 1 and prompt_rows[0].soul_event == "on_prompt"
              and prompt_rows[0].kind == "learning")

        # 4. Stop con 2 comandos → 2 filas on_turn_end (no se pierde ninguno).
        turn_rows = [r for r in rows if r.soul_event == "on_turn_end"]
        check("Stop con 2 scripts → 2 filas on_turn_end",
              len(turn_rows) == 2
              and {Path(r.script_path).name for r in turn_rows}
                  == {"memory_extraction_hook.py", "autodream_8gates.sh"})

        # 4b. DEFECTO DE ADA: dos FileChanged con el MISMO script y matchers distintos NO
        # colapsan — se conservan como dos filas con su matcher (o correría sobre el archivo
        # equivocado).
        fc_rows = [r for r in rows if r.soul_event == "on_file_change"]
        check("FileChanged: 2 filas, matchers preservados y distintos",
              len(fc_rows) == 2
              and {r.matcher for r in fc_rows} == {"jarvis_wakeup.jsonl", "nexus_inbox.jsonl"}
              and all(r.script_path.endswith("file_changed_context_hook.py") for r in fc_rows))

        # 5. coverage_report fail-closed: faltan on_boot y on_compact en el fixture.
        rep = coverage_report(rows)
        check("coverage marca learning incompleto cuando faltan críticos",
              rep["learning_complete"] is False
              and set(rep["missing_learning_events"]) == {"on_boot", "on_compact"})

    print()
    if fails:
        print(f"RESULTADO: {fails} fallo(s) ❌")
        return 1
    print("RESULTADO: TODO PASS ✅ — importador deriva del settings vivo sin descartes silenciosos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
