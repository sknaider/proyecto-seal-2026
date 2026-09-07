"""Re-corre los mutantes de nexus-credential-paths DENTRO de la arena."""
import json, pathlib, subprocess, sys, re
sys.path.insert(0, "/trabajo/tools")
import seal_mutacion_segura as sms

sms.verificar("/trabajo")
print("[arena] guarda: HABILITA")

# Las dependencias del proyecto salen del venv montado :ro. Un python:3.12-slim
# pelado no tiene pydantic y el CONTROL se pone rojo por falta de deps, no por un
# defecto -lo pisamos ALICE y yo por caminos distintos, 7-sep 15:17-. El montaje
# es de SOLO LECTURA, asi que la guarda lo permite y nada del venv puede mutarse.
LIBS = "/venv/lib/python3.12/site-packages"
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONPATH": LIBS,
       "PYTHONDONTWRITEBYTECODE": "1"}
TEST = "/trabajo/tests/test_nexus_credential_paths_v1.py"
SUJ = pathlib.Path("/trabajo/messages/session_checkpoint.py")

def corre():
    return subprocess.run([sys.executable, "-m", "pytest", TEST, "-q", "-p", "no:cacheprovider"],
                          capture_output=True, text=True, cwd="/trabajo", env=ENV).returncode

# El literal sale del propio test; NUNCA se imprime.
texto_test = pathlib.Path(TEST).read_text()
m = re.search(r'^LITERAL\s*=\s*(["\'])(.+?)\1', texto_test, re.M)
LITERAL = m.group(2)
print(f"[arena] literal tomado del test: {len(LITERAL)} caracteres (no se imprime)")

assert corre() == 0, "el CONTROL debe estar verde ANTES de mutar"
print("[arena] control: VERDE")

original = SUJ.read_text()
resultados = []
for ident, descripcion, nuevo in [
    ("checkpoint-vuelve-al-literal",
     'devolver DB_URL = "<literal>" como primera y unica via',
     'DB_URL = "%s"\n' % LITERAL + original),
    ("literal-como-default-de-environ-get",
     'el literal como default de os.environ.get',
     original.replace('os.environ.get("SEAL_DB_URL", "")',
                      'os.environ.get("SEAL_DB_URL", "%s")' % LITERAL)),
]:
    assert nuevo != original, f"{ident}: el mutante NO cambio el archivo"
    compile(nuevo, "m", "exec")
    SUJ.write_text(nuevo)
    rc = corre()
    SUJ.write_text(original)
    resultados.append({"id": ident, "subject": "messages/session_checkpoint.py",
                       "mutation": descripcion, "killed": rc != 0,
                       "killed_by": "test_el_checkpoint_no_contiene_el_literal_EN_NINGUNA_FORMA"})
    print(f"[arena] {ident}: rc={rc} -> {'MUERTO' if rc else 'SOBREVIVE'}")

assert corre() == 0, "el sujeto debe quedar restaurado y verde"
print("[arena] control final: VERDE")
pathlib.Path("/trabajo/resultado_mutacion.json").write_text(json.dumps(resultados, ensure_ascii=False))
print("[arena] RESULTADO", json.dumps([{k: r[k] for k in ("id", "killed")} for r in resultados]))
