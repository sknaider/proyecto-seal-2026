#!/usr/bin/env python3
"""Re-mutación por el REVISOR dentro de la arena aprobada (FABLE 7-sep 14:48). Formato de evidencia v2: cada mutante es
EJECUTABLE ({subject, ancla, reemplazo}) para que la próxima caducidad se re-corra sin re-autoría (decisión JARVIS 15:35).

Uso (dentro del contenedor, cwd=/trabajo, venv montado :ro en /venv):
  python3 tools/arena_remutar_revisor.py SPEC.json SALIDA.json
SPEC: {"change_id":..., "reviewer":..., "tests":[argv pytest relativo], "mutants":[{"id","subject","ancla","reemplazo","why"}]}
Reglas: la guarda del arnés se verifica ANTES de tocar nada; el control debe estar verde; cada mutante debe cambiar bytes;
el sujeto se restaura tras cada corrida; ninguna línea marcada # GUARDA-DESTRUCTIVA se muta (lo impone seal_mutacion_segura).
"""
from __future__ import annotations
import hashlib, json, pathlib, subprocess, sys, os

RAIZ = pathlib.Path("/trabajo") if pathlib.Path("/trabajo").is_dir() else pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "tools"))
import seal_mutacion_segura as sms  # noqa: E402

LIBS = "/venv/lib/python3.12/site-packages"
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONPATH": LIBS if pathlib.Path(LIBS).is_dir() else "", "PYTHONDONTWRITEBYTECODE": "1",
       **({"HOME": os.environ["HOME"]} if os.environ.get("HOME") else {})}  # HOME señuelo (555) del runner: chat_auth.py lo exige
# El ENV de arriba es una LISTA BLANCA: todo -e del docker run se descarta. Sin esta linea, un sujeto que pide
# configuracion al IMPORTAR (memory/memory_extraction_hook.py -> seal_secrets.pg_dsn) sale rojo POR ENTORNO, y un rojo
# de entorno es indistinguible de un mutante vivo. Pasa SOLO los senuelos declarados. (ALICE, 7-sep, revisando soul-f2.)
ENV.update({k: v for k, v in os.environ.items() if k in {"SEAL_DB_DSN"} and v})


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


ULTIMA_SALIDA = ""


_PROHIBIDOS_EN_TESTS = {"python3", "python", "pytest", "-m", "py.test"}

def _validar_tests(tests):
    """Cada entrada de spec["tests"] es un argv PARA pytest (rutas y flags), no un comando.
    Un "python3 -m pytest ..." acá hace que pytest busque un archivo llamado python3
    y falle con un mensaje que confunde (dos clones lo leyeron como PYTHONPATH roto, 7-sep)."""
    if not isinstance(tests, list) or not tests or not all(isinstance(a, list) and a for a in tests):
        raise SystemExit("spec['tests'] debe ser una lista no vacia de listas argv para pytest")
    for argv in tests:
        malos = [t for t in argv if t in _PROHIBIDOS_EN_TESTS]
        if malos:
            raise SystemExit(f"spec['tests'] lleva {malos}: son argv PARA pytest, sin interprete ni '-m pytest' (ej. [['messages/tests/test_x.py']])")
        for t in argv:
            if t.startswith("-") or not (t.endswith(".py") or "::" in t or "/" in t):
                continue  # flags y sus valores (-k "expr", -q) no son rutas
            ruta = t.split("::")[0]
            if not (RAIZ / ruta).exists():
                raise SystemExit(f"spec['tests'] apunta a un archivo inexistente en la arena: {ruta}")

def corre(tests: list[list[str]]) -> int:
    global ULTIMA_SALIDA
    rc = 0
    ULTIMA_SALIDA = ""
    for argv in tests:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *argv], capture_output=True, text=True, cwd=str(RAIZ), env=ENV)
        ULTIMA_SALIDA += (r.stdout + r.stderr)[-3000:]
        rc = rc or r.returncode
    return rc


def main(spec_path: str, salida: str) -> int:
    spec = json.loads(pathlib.Path(spec_path).read_text())
    sms.verificar(str(RAIZ))
    print("[arena] guarda: HABILITA")
    tests = spec["tests"]
    _validar_tests(tests)
    if corre(tests) != 0:
        print("[arena] CONTROL ROJO; cola de pytest:\n" + "\n".join(ULTIMA_SALIDA.splitlines()[-25:]))
        raise SystemExit("el CONTROL debe estar verde ANTES de mutar")
    print("[arena] control: VERDE")
    control = {"rc": 0, "pytest_tail": "\n".join(ULTIMA_SALIDA.splitlines()[-4:]), "sha_sujetos_antes": {m["subject"]: sha(RAIZ / m["subject"]) for m in spec["mutants"]}}
    resultados = []
    for m in spec["mutants"]:
        suj = RAIZ / m["subject"]
        original = suj.read_text()
        if sms.MARCA_GUARDA in m["ancla"] or any(sms.MARCA_GUARDA in l for l in original.splitlines() if m["ancla"] in l):
            raise SystemExit(f"{m['id']}: el ancla toca una linea {sms.MARCA_GUARDA}; prohibido")
        # aplicar_y_registrar (NEXUS 15:22): ancla EXACTAMENTE 1 vez, devuelve el registro reproducible (sha del ancla y del sujeto)
        nuevo, registro = sms.aplicar_y_registrar(original, m["ancla"], m["reemplazo"], arena=str(RAIZ), sujeto=m["subject"])
        n = 1
        assert nuevo != original, f"{m['id']}: el mutante NO cambio el archivo"
        if suj.suffix == ".py":
            compile(nuevo, str(suj), "exec")
        suj.write_text(nuevo)
        try:
            rc = corre(tests)
        finally:
            suj.write_text(original)
        assert sha(suj) == hashlib.sha256(original.encode()).hexdigest(), f"{m['id']}: el sujeto no quedo restaurado"
        restaurado = sha(suj) == control["sha_sujetos_antes"][m["subject"]]
        # trazabilidad (pedido ADA 15:48): qué brazos mataron al mutante y la cola de pytest, no sólo el rc
        fallos = [l.split()[1] for l in ULTIMA_SALIDA.splitlines() if l.startswith("FAILED ") and len(l.split()) > 1]
        resultados.append({**m, "registro": registro, "result": "KILLED" if rc else "SURVIVED", "rc": rc,
                           "killed_by": fallos[:12], "pytest_tail": "\n".join(ULTIMA_SALIDA.splitlines()[-6:]), "sujeto_restaurado": restaurado})
        print(f"[arena] {m['id']}: rc={rc} -> {'MUERTO' if rc else 'SOBREVIVE'}")
    killed = sum(1 for r in resultados if r["result"] == "KILLED")
    # file_sha256 sobre TODOS los subjects+tests del manifiesto (el gate compara el dict completo)
    files = sorted({m["subject"] for m in spec["mutants"]} | set(spec.get("extra_files", [])) | {t for argv in tests for t in argv if t.endswith(".py") and "::" not in t} | {t.split("::")[0] for argv in tests for t in argv if "::" in t})
    if spec.get("manifest") and (RAIZ / spec["manifest"]).is_file():
        man = json.loads((RAIZ / spec["manifest"]).read_text()); files = sorted(set(files) | set(man.get("subjects", [])) | set(man.get("tests", [])))
    ev = {"schema": "seal.mutation-evidence.v1", "formato_mutantes": "ejecutable-v2 (ancla/reemplazo/sha; decision JARVIS 15:35)", "change_id": spec["change_id"], "reviewer": spec["reviewer"], "mutation_target": "arena_aprobada_fable_20260907",
          "tool": "tools/arena_remutar_revisor.py", "killed": killed, "survived": len(resultados) - killed, "total": len(resultados), "no_tests": 0, "skipped": 0, "suspicious": 0, "timeout": 0,
          "mutation_score_percent": round(100.0 * killed / max(1, len(resultados)), 3), "control_sujeto_sin_mutar": control,
          "limpieza": {"sujetos_restaurados": all(r["sujeto_restaurado"] for r in resultados), "arena": "borrada por el runner al terminar (tools/arena_remutar_run.sh)"}, "mutants": resultados,
          "file_sha256": {f: sha(RAIZ / f) for f in files if (RAIZ / f).is_file()}}
    pathlib.Path(salida).write_text(json.dumps(ev, indent=2, ensure_ascii=False) + "\n")
    print(f"[arena] evidencia v2 -> {salida}: {killed}/{len(resultados)} muertos")
    return 0 if killed == len(resultados) else 5


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
