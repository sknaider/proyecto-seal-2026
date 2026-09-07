#!/usr/bin/env bash
# Dentro de la arena: corre los arneses de mutación PROPIOS de un manifiesto (memory/tests/check_*_mutants.py) y deja
# su salida combinada en quality/mutantes/salida.json para que tools/arena_remutar_run.sh la copie.
# Uso (vía SEAL_ARENA_CMD): bash tools/arena_recorrer_checks_mutantes.sh <check1.py> [<check2.py> ...]
set -u
mkdir -p quality/mutantes; : > quality/mutantes/_partes.txt; RC=0
for c in "$@"; do
  n=$(basename "$c" .py); python3 "$c" > "quality/mutantes/$n.json" 2> "quality/mutantes/$n.err"; r=$?; [ $r -eq 0 ] || RC=$r
  echo "$n rc=$r" >&2; echo "$n" >> quality/mutantes/_partes.txt
done
python3 - <<'PY'
import json,pathlib
out={}
for n in pathlib.Path('quality/mutantes/_partes.txt').read_text().split():
    p=pathlib.Path(f'quality/mutantes/{n}.json')
    try: out[n]=json.loads(p.read_text())
    except Exception as e: out[n]={"error":type(e).__name__,"stderr_tail":pathlib.Path(f'quality/mutantes/{n}.err').read_text()[-600:]}
import os
# si un check escribe su evidencia a un archivo (p. ej. quality/mutation-<caso>.json) en vez de stdout, SEAL_ARENA_EVIDENCIA lo nombra
ev=os.environ.get('SEAL_ARENA_EVIDENCIA')
if ev and pathlib.Path(ev).is_file():
    try: out['evidencia_escrita']=json.loads(pathlib.Path(ev).read_text())
    except Exception as e: out['evidencia_escrita']={"error":type(e).__name__}
pathlib.Path('quality/mutantes/salida.json').write_text(json.dumps(out,indent=1,ensure_ascii=False)+"\n")
PY
exit $RC
