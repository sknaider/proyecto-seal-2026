#!/usr/bin/env bash
# Recorrido COMPLETO del arnes de mutacion, extremo a extremo, sobre datos
# desechables. Cierra la condicion de ADA (7-sep 14:01): no alcanza con que la
# guarda niegue; hay que ver el ciclo entero UNA VEZ HABILITADO, incluida la
# limpieza, sin montar recursos reales.
#
#   1. arena = copia desde el INDICE de git (no HEAD: HEAD excluye lo staged)
#   2. contenedor sin privilegios, SOLO la arena montada rw
#   3. control verde -> mutante -> el test debe MATARLO
#   4. limpieza de la arena
#   5. el arbol vivo debe quedar intacto (se compara sha256 antes/despues)
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=$(pwd)

huella() { find tools tests -type f -name '*.py' -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-16; }
ANTES=$(huella)

ARENA=$(mktemp -d /tmp/seal-arena-XXXXXX)
git archive "$(git write-tree)" | tar -x -C "$ARENA"
# La arena tiene que ser LEGIBLE por el uid sin privilegios. `mktemp -d` la crea
# en 700 y el contenedor como 65534 no puede ni abrirla: da ModuleNotFoundError,
# que parece un error de rutas y es de permisos (medido 14:02). ALICE lo habia
# dicho -su copia tenia "permisos abiertos"- y yo lo tome por un detalle de su
# prueba: es parte de la receta.
chmod -R a+rwX "$ARENA"   # rw: el mutante se ESCRIBE en la arena
echo "arena:  $ARENA  (desde el INDICE, $(find "$ARENA" -type f | wc -l) archivos, legible por el uid sin privilegios)"

SALIDA=$(mktemp /tmp/seal-arena-salida-XXXXXX)
(docker run --rm -i --user 65534:65534 -e PYTHONDONTWRITEBYTECODE=1 --tmpfs /libs:rw,exec -v "$ARENA":/trabajo -w /trabajo python:3.12-slim \
  python - <<'PY'
import subprocess, sys, pathlib
sys.path.insert(0, "/trabajo/tools")
import seal_mutacion_segura as s

s.verificar("/trabajo")
print("  [1] guarda: HABILITA (arena aislada)")

# Nada de HOME escribible: darle HOME=/tmp hace que `Path.home()` devuelva
# /tmp y el freno 1 NIEGA, con razon -es una raiz protegida escribible-. La
# salida no es aflojar el freno sino no necesitar HOME: pip sin cache, hacia un
# destino dentro de la propia arena (medido 14:03).
# Las librerias y el cache van a un tmpfs del contenedor, NO a la arena.
# Un archivo nuevo creado por el uid 65534 dentro de la arena NO lo puede
# borrar despues el usuario del host: la limpieza fallaba con "Permiso
# denegado" sobre .pytest_cache (medido 14:05). El mutante si va a la
# arena, pero SOBREESCRIBE un archivo que ya existe: no cambia de dueno.
LIBS = "/libs"
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--no-cache-dir",
                "--target", LIBS, "pytest"], check=True)
sys.path.insert(0, LIBS)
T = "/trabajo/tools/tests/test_seal_mutacion_segura_v1.py"

def corre():
    # El env explicito DESCARTA lo que viene por -e: sin repetir aqui
    # PYTHONDONTWRITEBYTECODE, pytest dejaba .pyc en la arena que el host no
    # podia borrar. Un entorno explicito no hereda; hay que ponerlo entero.
    entorno = {"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONPATH": LIBS,
               "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-m", "pytest", T, "-q", "-p", "no:cacheprovider"],
                          capture_output=True, text=True, env=entorno).returncode

assert corre() == 0, "el CONTROL tiene que estar verde ANTES de mutar"
print("  [2] control: VERDE")

obj = pathlib.Path("/trabajo/tools/seal_mutacion_segura.py")
fuente = obj.read_text()
ancla = 'if tipo in _FS_SIN_ESTADO_DEL_HOST:'
assert fuente.count(ancla) == 1, "el ancla debe ser unica"
mutante = s.mutar(fuente, ancla, 'if True:', arena="/trabajo")
assert mutante != fuente and compile(mutante, "m", "exec")
obj.write_text(mutante)
print("  [3] mutante aplicado: se ignora el tipo de fs (todo montaje se descarta)")

rc = corre()
print(f"  [4] con el mutante los tests dan rc={rc} -> " +
      ("MUERTO, el test lo detecta" if rc != 0 else "SOBREVIVE: hueco en el test"))
# La marca solo se imprime si el mutante MURIO. Es lo que el paso de afuera
# exige ver: un recorrido que no imprime su marca no cuenta como hecho.
if rc != 0:
    print("  RECORRIDO INTERNO OK")
sys.exit(0 if rc != 0 else 1)
PY
) | tee "$SALIDA"

# El contenedor tiene que DECIR que corrio. Sin -i, `docker run` no conecta
# stdin: `python -` leyo vacio, salio 0 y el recorrido dio "OK" sin ejecutar
# nada (medido 14:02). Un paso que no imprime su marca no cuenta como hecho.
grep -q "RECORRIDO INTERNO OK" "$SALIDA" || {
  echo "  el contenedor no dejo su marca: no se ejecuto el recorrido"; exit 1; }

echo "limpieza:"
find "$ARENA" -mindepth 1 -delete && rmdir "$ARENA"
[ -d "$ARENA" ] && { echo "  la arena NO se limpio"; exit 1; }
echo "  arena borrada: $ARENA"

DESPUES=$(huella)
[ "$ANTES" = "$DESPUES" ] || { echo "  EL ARBOL VIVO CAMBIO ($ANTES -> $DESPUES)"; exit 1; }
echo "  arbol vivo intacto: sha256 $ANTES == $DESPUES"
echo "RECORRIDO COMPLETO OK"
