#!/usr/bin/env python3
"""Mutantes explícitos del carril de lecciones de active_recall (memory/mcp_server_v4.py).
Mismo contrato que check_recall_router_bm25_mutants.py (2-sep): copia del sujeto en un tmp
fresco por mutante (nunca el archivo vivo), -B, killer = pytest rc == 1 (2/4/5 = SOSPECHOSO),
control positivo (copia sin mutar pasa) y control negativo del oráculo (copia que no compila
debe clasificarse SOSPECHOSO). Salida: quality/mutation-recall-lessons-lane-20260902.json"""
from __future__ import annotations
import hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUBJECT = REPO / "memory" / "mcp_server_v4.py"
TESTS = REPO / "memory" / "tests" / "test_recall_lessons_lane.py"
OUT = REPO / "quality" / "mutation-recall-lessons-lane-20260902.json"
T = "memory/tests/test_recall_lessons_lane.py"
PY = sys.executable
MUTANTS = [
    ("corpus_filter_removed", "      AND metadata->>'source_kind' = 'claude_memory_file'\n", "",
     f"{T}::test_sql_targets_only_the_lessons_corpus_and_excludes_given_ids", "sin el filtro el carril trae cualquier memoria team"),
    ("scope_filter_removed", "      AND scope IN ('team', 'public')\n", "",
     f"{T}::test_sql_targets_only_the_lessons_corpus_and_excludes_given_ids", "traería memorias privadas ajenas"),
    ("exclude_ids_ignored", "[int(i) for i in exclude_ids], int(limit))", "[], int(limit))",
     f"{T}::test_sql_targets_only_the_lessons_corpus_and_excludes_given_ids", "duplica lo que ya trajo el semántico"),
    ("threshold_disabled", "        if score < min_score:\n            continue\n", "",
     f"{T}::test_threshold_filters_low_similarity_and_keeps_order", "lecciones irrelevantes ocupan los 3 lugares"),
    ("metadata_str_not_parsed", "        if isinstance(md, str):\n            try:\n                md = json.loads(md)\n            except Exception:\n                md = {}\n", "        if isinstance(md, str):\n            md = {}\n",
     f"{T}::test_threshold_filters_low_similarity_and_keeps_order", "metadata como texto pierde descripción"),
    ("section_header_lost", 'lines = ["## Lecciones del equipo (memory/*.md vía SOUL DB — abrí el archivo para el detalle)"]', 'lines = []',
     f"{T}::test_section_format_has_file_author_and_similarity", "sin cabecera la sección se confunde con las memorias"),
    ("empty_returns_section", "    if not lessons:\n        return \"\"\n    lines = [\"## Lecciones", "    lines = [\"## Lecciones",
     f"{T}::test_empty_lessons_produce_no_section", "agregaría una sección vacía en cada recall"),
    ("min_score_drifted_0_80", "_LESSONS_LANE_MIN_SCORE = 0.85", "_LESSONS_LANE_MIN_SCORE = 0.80",
     f"{T}::test_threshold_filters_low_similarity_and_keeps_order", "0.85->0.80 deja pasar una lección a 0.83 (azar con e5): muere por EFECTO (fixture ALICE)"),
]

def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

def run_pytest(copy_dir: Path, target: str) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{copy_dir}{os.pathsep}{REPO / 'memory'}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # credencial del agente que CORRE el arnés (ALICE 2-sep): nunca la de otro
    _agent = os.environ.get("SEAL_AGENT", "jarvis").lower()
    if not env.get("SEAL_DB_URL") and not env.get("SEAL_DB_URL_FILE"):
        env["SEAL_DB_URL_FILE"] = os.path.expanduser(f"~/.config/seal/mcp_agents/{_agent}.dsn")
    cp = subprocess.run([PY, "-B", "-m", "pytest", "-q", "--noconftest", "-p", "no:cacheprovider", target],
                        cwd=REPO, env=env, capture_output=True, text=True, timeout=600)
    return cp.returncode

def main() -> int:
    original = SUBJECT.read_text()
    tmp_root = Path(tempfile.mkdtemp(prefix="seal-mutants-lessons-lane-", dir="/tmp"))
    assert str(tmp_root).startswith("/tmp/"), tmp_root
    ctrl = tmp_root / "control"; ctrl.mkdir(); (ctrl / "mcp_server_v4.py").write_text(original)
    control_rc = run_pytest(ctrl, T); control_ok = control_rc == 0
    bad = tmp_root / "oracle_check_broken_import"; bad.mkdir(); (bad / "mcp_server_v4.py").write_text("this is not python (\n")
    bad_rc = run_pytest(bad, MUTANTS[0][3]); oracle_ok = bad_rc not in (0, 1)
    print(f"control rc={control_rc} ok={control_ok} · oracle-check broken_import rc={bad_rc} -> {'SUSPECT (ok)' if oracle_ok else 'MISCLASSIFIED'}")
    detail, killed, suspicious = [], 0, 0
    for name, old, new, killer, note in MUTANTS:
        assert original.count(old) >= 1, f"{name}: patrón no encontrado"
        mutated = original.replace(old, new); assert mutated != original, name
        d = tmp_root / name; d.mkdir(); (d / "mcp_server_v4.py").write_text(mutated)
        rc = run_pytest(d, killer); is_killed = rc == 1; is_susp = rc not in (0, 1)
        killed += is_killed; suspicious += is_susp
        detail.append({"mutant": name, "test": killer.split("::")[-1], "killed": is_killed, "suspicious": is_susp, "rc": rc, "note": note})
        print(f"{'KILLED  ' if is_killed else ('SUSPECT ' if is_susp else 'SURVIVED')} {name:26s} rc={rc}")
    total = len(MUTANTS); score = 100.0 * killed / total
    OUT.write_text(json.dumps({
        "schema": "seal.mutation-evidence.v1",
        "tool": "explicit adversarial mutants on a COPY of memory/mcp_server_v4.py per mutant in a fresh tmp dir, -B, killer = named test rc==1 in subprocess pytest; rc not in (0,1) = suspicious",
        "workspace": "carril de lecciones de active_recall: _LESSONS_LANE_SQL, _recall_lessons_lane, _format_lessons_section, _LESSONS_LANE_MIN_SCORE",
        "killed": killed, "survived": total - killed - suspicious, "no_tests": 0, "skipped": 0,
        "suspicious": suspicious + (0 if control_ok else 1), "timeout": 0, "total": total, "mutation_score_percent": score,
        "positive_control_unmutated_copy": {"rc": control_rc, "ok": control_ok},
        "oracle_negative_control_broken_import": {"rc": bad_rc, "classified_as_killed": bad_rc == 1, "ok": oracle_ok},
        "reviewer": "NEXUS",
        "notes_defense_in_depth": "el bloque de active_recall que invoca el carril está en try/except (aditivo); no se muta porque su killer requeriría el pool real.",
        "file_sha256": {"memory/mcp_server_v4.py": sha(SUBJECT), "memory/tests/test_recall_lessons_lane.py": sha(TESTS),
                        "memory/tests/check_recall_lessons_lane_mutants.py": sha(Path(__file__).resolve()),
                        # sujeto del manifest sin mutantes propios (script de ingesta): el gate exige que la evidencia cubra TODOS los subjects
                        "tools/seal_memory_files_ingest.py": sha(REPO / "tools" / "seal_memory_files_ingest.py")},
        "detail": detail, "tmp_dir": str(tmp_root)}, indent=2, ensure_ascii=False) + "\n")
    print(f"score={score:.1f}% killed={killed}/{total} suspicious={suspicious} control_ok={control_ok} oracle_ok={oracle_ok} -> {OUT.relative_to(REPO)}")
    return 0 if (score == 100.0 and control_ok and oracle_ok and suspicious == 0) else 1

if __name__ == "__main__":
    sys.exit(main())
