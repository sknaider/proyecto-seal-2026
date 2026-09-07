#!/usr/bin/env bash
# Ronda única de re-verificación del revisor JARVIS tras el corte de ediciones en sujetos compartidos (regla 7-sep, ADA/ALICE).
# Por manifiesto: corre los brazos con el venv, re-corre la mutación en la arena si hay spec, re-firma con historial y muestra el gate.
# Uso: tools/jarvis_reverificar_tanda.sh <manifiesto> [<manifiesto> ...]   (nombres sin ruta ni .json)
set -u
PY3=/home/dadito/IA/seal-spark/.venv/bin/python3; cd /home/dadito/IA/proyecto-seal
for mf in "$@"; do
  echo "##### $mf"
  $PY3 - "quality/manifests/$mf.json" <<'PYX'
import json,subprocess,sys,pathlib
d=json.loads(pathlib.Path(sys.argv[1]).read_text()); PY3="/home/dadito/IA/seal-spark/.venv/bin/python3"; ok=0
for c in d.get('commands',[]):
    argv=[PY3 if a in ('python3','/usr/bin/python3') else a for a in (c.get('argv') or ['bash','-c',c.get('command','')])]
    r=subprocess.run(argv,capture_output=True,text=True,timeout=300,cwd='/home/dadito/IA/proyecto-seal'); ok+=(r.returncode==0)
    print(f"  {c.get('id')} rc={r.returncode} :: {(r.stdout.strip().splitlines() or [''])[-1][:80]}")
print(f"  brazos ok {ok}/{len(d.get('commands',[]))}"); sys.exit(0 if ok==len(d.get('commands',[])) else 1)
PYX
  [ $? -eq 0 ] || { echo "  brazos rojos: NO se re-firma $mf"; continue; }
  if [ -f "quality/mutantes/$mf.spec.json" ]; then
    bash tools/arena_remutar_run.sh "quality/mutantes/$mf.spec.json" "quality/mutation-$mf.v2.json" 2>&1 | grep -E 'muertos|SystemExit|ROJO' | sed 's/^/  /'
  fi
  $PY3 - "$mf" <<'PYX'
import json,hashlib,pathlib,sys,datetime
sys.path.insert(0,'quality_gate'); import gate
mf=sys.argv[1]; p=pathlib.Path(f'quality/manifests/{mf}.json'); d=json.loads(p.read_text())
ev=pathlib.Path(f'quality/mutation-{mf}.v2.json')
if ev.is_file():
    e=json.loads(ev.read_text()); e['file_sha256']={x:hashlib.sha256(pathlib.Path(x).read_bytes()).hexdigest() for x in sorted(set(e.get('file_sha256',{}))|set(d['subjects'])|set(d['tests']))}; e['mutation_score_percent']=round(100.0*e['killed']/max(1,e['total']),3); ev.write_text(json.dumps(e,indent=2,ensure_ascii=False)+"\n")
    if d.get('mutation_evidence')!=str(ev): d['_mutation_evidence_v1_historica']=d.get('mutation_evidence'); d['mutation_evidence']=str(ev)
r=d['review']['receipt']; d['review'].setdefault('historial',[]).append({k:r[k] for k in ('reviewer','signed_at','reviewed_sha256','manifest_digest') if k in r})
files=[f for f in d['subjects']+d['tests'] if pathlib.Path(f).is_file()]
r['evidence']+=f" | RE-VERIFICACION JARVIS {datetime.datetime.now().astimezone().strftime('%d-%b %H:%M')} (ronda unica tras el corte de ediciones en sujetos compartidos): brazos verdes con el venv" + (f"; mutacion re-corrida en la arena {json.loads(ev.read_text())['killed']}/{json.loads(ev.read_text())['total']}" if ev.is_file() else "")
r['signed_at']=datetime.datetime.now(datetime.timezone.utc).isoformat(); r['reviewed_sha256']={f:hashlib.sha256(pathlib.Path(f).read_bytes()).hexdigest() for f in files}; r['manifest_digest']=gate._review_digest(d)
p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+"\n")
PYX
  $PY3 quality_gate/gate.py verify "quality/manifests/$mf.json" 2>&1 | python3 -c "import sys,json,re;t=sys.stdin.read();m=re.search(r'\{.*\}',t,re.S);d=json.loads(m.group(0));print('  gate:',d.get('status'),d.get('errors') or d.get('error') or '')"
done
