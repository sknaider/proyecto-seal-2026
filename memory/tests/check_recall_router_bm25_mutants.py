#!/usr/bin/env python3
"""Mutantes explícitos del carril memories de recall_router (D2, 2-sep-2026).

Corre cada mutante sobre una COPIA de memory/recall_router.py en un directorio
temporal (nunca el archivo vivo), en un subproceso pytest con la copia primero en
PYTHONPATH y sin bytecode (evita el .pyc de igual tamaño, lección 31-ago). Un
mutante está MUERTO si su test asesino falla (rc != 0). Control positivo: la copia
SIN mutar debe pasar los mismos tests (si no, el arnés no toca al sujeto).

Salida: quality/mutation-recall-router-bm25-fallback-20260902.json
(schema seal.mutation-evidence.v1). Exit 0 sólo si score == 100 y control OK.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUBJECT = REPO / "memory" / "recall_router.py"
TESTS = REPO / "memory" / "tests" / "test_recall_router_memories_bm25_fallback.py"
OUT = REPO / "quality" / "mutation-recall-router-bm25-fallback-20260902.json"
PY = sys.executable
T = "memory/tests/test_recall_router_memories_bm25_fallback.py"

MUTANTS = [
    ("stopwords_removed", "or tok in _BM25_STOPWORDS:", "or False:",
     f"{T}::test_or_terms_builder_drops_stopwords_so_the_or_slots_discriminate",
     "sin el filtro, 'del','las','uno' vuelven a ocupar lugares del OR (EXPLAIN 2-sep: 25.670 filas descartadas por RLS)"),
    ("min_token_len_zero", "_BM25_MIN_TOKEN_LEN = 3", "_BM25_MIN_TOKEN_LEN = 0",
     f"{T}::test_or_terms_builder_drops_short_tokens_and_caps",
     "'y','a' entran al OR"),
    ("or_cap_removed", "_BM25_OR_MAX_TERMS = 8", "_BM25_OR_MAX_TERMS = 1000",
     f"{T}::test_or_terms_builder_drops_short_tokens_and_caps",
     "OR sin tope: medido OR12 294 ms vs OR8 218 ms"),
    ("and_threshold_removed", "_BM25_AND_MAX_TOKENS = 5", "_BM25_AND_MAX_TOKENS = 100000",
     f"{T}::test_long_query_goes_straight_to_or",
     "la query larga vuelve a pagar el AND inútil (0/40) antes del OR"),
    ("or_fallback_removed", "        if not rows:\n            or_terms = _bm25_or_terms(query)",
     "        if False:\n            or_terms = _bm25_or_terms(query)",
     f"{T}::test_falls_back_to_or_when_and_is_empty",
     "sin fallback: el carril vuelve a estar muerto para texto libre"),
    ("rank_normalization_dropped", "websearch_to_tsquery('simple', $1), 32)", "websearch_to_tsquery('simple', $1), 0)",
     f"{T}::test_sql_ranks_by_relevance_weighted_by_importance",
     "ts_rank con normalización 0 no acota ni penaliza longitud (duda de FABLE 2-sep)"),
    ("importance_as_key",
     "ORDER BY ts_rank(embedding_bm25, websearch_to_tsquery('simple', $1), 32)\n                     * (0.5 + COALESCE(importance, 5) / 20.0) DESC,\n                     importance DESC",
     "ORDER BY importance DESC, kw_rank DESC",
     f"{T}::test_sql_ranks_by_relevance_weighted_by_importance",
     "vuelve al orden viejo: la relevancia sólo desempata"),
    ("lane_timeout_regressed", "TIMEOUT_MEMORIES_BM25 = 1.00", "TIMEOUT_MEMORIES_BM25 = 0.35",
     f"{T}::test_memories_lane_timeout_covers_the_cold_or_query",
     "0.35/0.60 cortaban la consulta OR (993 ms en frío tras el reboot)"),
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_pytest(copy_dir: Path, target: str) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{copy_dir}{os.pathsep}{REPO / 'memory'}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cp = subprocess.run(
        [PY, "-B", "-m", "pytest", "-q", "--noconftest", "-p", "no:cacheprovider", target],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=300,
    )
    return cp.returncode


def main() -> int:
    original = SUBJECT.read_text()
    tmp_root = Path(tempfile.mkdtemp(prefix="seal-mutants-recall-router-", dir="/tmp"))
    assert str(tmp_root).startswith("/tmp/"), tmp_root  # guard: nunca fuera de /tmp
    detail, killed, suspicious = [], 0, 0

    # control positivo: copia sin mutar debe pasar todos los tests asesinos
    ctrl_dir = tmp_root / "control"; ctrl_dir.mkdir()
    (ctrl_dir / "recall_router.py").write_text(original)
    control_rc = run_pytest(ctrl_dir, T)
    control_ok = control_rc == 0

    # control NEGATIVO del oráculo (H2): copia que NI COMPILA -> el runner debe decir
    # SOSPECHOSO, no KILLED. Si lo clasificara muerto, el 100 % no valdría nada.
    bad_dir = tmp_root / "oracle_check_broken_import"; bad_dir.mkdir()
    (bad_dir / "recall_router.py").write_text("this is not python (\n")
    bad_rc = run_pytest(bad_dir, MUTANTS[0][3])
    oracle_ok = bad_rc not in (0, 1)   # 2/4 esperados; ni verde ni "tests fallaron"
    print(f"oracle-check broken_import rc={bad_rc} -> {'SUSPECT (ok)' if oracle_ok else 'MISCLASSIFIED'}")

    for name, old, new, killer, note in MUTANTS:
        assert original.count(old) >= 1, f"{name}: patrón no encontrado -> el mutante no toca al sujeto"
        mutated = original.replace(old, new)
        assert mutated != original, f"{name}: bytes idénticos"
        d = tmp_root / name; d.mkdir()
        (d / "recall_router.py").write_text(mutated)
        rc = run_pytest(d, killer)
        # H2 (revisión NEXUS 2-sep): pytest rc 1 = tests fallaron; 2 interrumpido, 3 interno,
        # 4 uso, 5 nada recolectado. Sólo el 1 significa "el test lo mató"; el resto es un
        # mutante que no corrió y se anota SOSPECHOSO, nunca muerto.
        is_killed = rc == 1
        is_suspicious = rc not in (0, 1)
        killed += int(is_killed); suspicious += int(is_suspicious)
        detail.append({"mutant": name, "test": killer.split("::")[-1], "killed": is_killed,
                       "suspicious": is_suspicious, "rc": rc, "replaced": old[:60], "with": new[:60], "note": note})
        print(f"{'KILLED  ' if is_killed else ('SUSPECT ' if is_suspicious else 'SURVIVED')} {name:28s} rc={rc}")

    total = len(MUTANTS)
    score = 100.0 * killed / total
    evidence = {
        "schema": "seal.mutation-evidence.v1",
        "tool": "explicit adversarial mutants (constant regression + text mutation of SQL/branches) on a COPY of memory/recall_router.py in a fresh tmp dir per mutant, -B/PYTHONDONTWRITEBYTECODE (never the live file, never a reused .pyc); killer = named test goes red in a subprocess pytest",
        "workspace": "carril memories de recall_router: _bm25_or_terms (stopwords, min len, cap), _recall_memories (umbral AND, fallback OR), _MEMORIES_BM25_SQL (ts_rank 32, ORDER BY relevancia x importancia), TIMEOUT_MEMORIES_BM25",
        "killed": killed, "survived": total - killed, "no_tests": 0, "skipped": 0,
        "suspicious": suspicious + (0 if control_ok else 1), "timeout": 0, "total": total,
        "oracle_negative_control_broken_import": {"rc": bad_rc, "classified_as_killed": bad_rc == 1,
                                                  "ok": oracle_ok, "meaning": "un mutante que no corre debe ser SOSPECHOSO, no muerto (H2 NEXUS 2-sep)"},
        "mutation_score_percent": score,
        "positive_control_unmutated_copy": {"rc": control_rc, "ok": control_ok,
                                            "meaning": "si la copia sin mutar fallara, el arnés no estaría tocando al sujeto"},
        "reviewer": "NEXUS",
        "notes_defense_in_depth": "el except Exception -> [] del carril es defense-in-depth compartido por todos los carriles del router (_safe); no se muta porque su killer requeriría un pool que lance, cubierto por el timeout del _safe.",
        "file_sha256": {"memory/recall_router.py": sha(SUBJECT),
                        "memory/tests/test_recall_router_memories_bm25_fallback.py": sha(TESTS),
                        # el gate exige que la evidencia cubra TODO subjects+tests del manifest,
                        # y este arnés está declarado en tests (2-sep 12:26: mutation_evidence_stale)
                        "memory/tests/check_recall_router_bm25_mutants.py": sha(Path(__file__).resolve())},
        "detail": detail,
        "tmp_dir": str(tmp_root),
    }
    OUT.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    print(f"score={score:.1f}% killed={killed}/{total} suspicious={suspicious} control_ok={control_ok} oracle_ok={oracle_ok} -> {OUT.relative_to(REPO)}")
    return 0 if (score == 100.0 and control_ok and oracle_ok and suspicious == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
