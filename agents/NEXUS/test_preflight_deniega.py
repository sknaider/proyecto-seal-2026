"""Control de DOS BRAZOS por cada arm del preflight.

Regla: cada caso peligroso se corre TAMBIEN en su version sana. Si el sano no da
PASS, el test no prueba que el gate detecte: prueba que rechaza todo.
"""
import sys, tempfile, types
from pathlib import Path
sys.path.insert(0, "/home/dadito/IA/proyecto-seal/agents/NEXUS")
import gate_arranque_clon as G

SANO = """ExecStart=/usr/bin/docker run --rm --name c --network host --read-only \
--user 1000:1000 --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 128 \
--memory 4g --cpus 2 \
--mount type=bind,src=/x/.agent_session_token_NEXUS-u103,dst=/run/secrets/instance-token,readonly \
--mount type=bind,src=/p/technical.sqlite3,dst=/opt/seal/projection/technical.sqlite3,readonly \
seal-user-clone:1.2.0 --agent NEXUS"""

def cfg(**kw):
    c = types.SimpleNamespace(agente="NEXUS", usuario="u103", sha_proyeccion=None)
    for k, v in kw.items(): setattr(c, k, v)
    return c

CASOS = [
    ("102 token de OTRO agente",  G.brazo_p2_token_correcto_montado,
     SANO.replace("token_NEXUS-u103", "token_ALICE-u103")),
    ("103 proyeccion SIN readonly", G.brazo_p3_proyeccion,
     SANO.replace("technical.sqlite3,readonly", "technical.sqlite3,rw")),
    ("104 sin --read-only",       G.brazo_p4_aislamiento, SANO.replace("--read-only ", "")),
    ("104 sin cap-drop",          G.brazo_p4_aislamiento, SANO.replace("--cap-drop ALL ", "")),
    ("104 corre como root",       G.brazo_p4_aislamiento, SANO.replace("--user 1000:1000 ", "")),
    ("105 DSN en el arranque",    G.brazo_p5_sin_credenciales,
     SANO + " -e SEAL_DSN=postgresql://seal:x@localhost:5433/seal_memory"),
    ("105 variable PASSWORD",     G.brazo_p5_sin_credenciales, SANO + " --env DB_PASSWORD=x"),
    ("106 imagen :latest",        G.brazo_p6_imagen_fijada,
     SANO.replace("seal-user-clone:1.2.0", "seal-user-clone:latest")),
]

# La proyeccion real tiene que existir para que 103 llegue a mirar el readonly.
proy = Path("/home/dadito/.local/share/seal/user-clone-projections/technical.sqlite3")
if not proy.exists():
    print("  (aviso: sin proyeccion real, el caso 103 no es concluyente)")

ok = True
print("\n  CASO PELIGROSO                    veredicto   |  control SANO")
print("  " + "-"*66)
for nombre, fn, txt_malo in CASOS:
    malo = fn(cfg(), txt_malo).veredicto
    sano = fn(cfg(), SANO).veredicto
    marca_m = "FALLA ✓" if malo == G.FAIL else f"{malo} ✗"
    marca_s = "PASS ✓" if sano == G.PASS else f"{sano} ✗"
    print(f"  {nombre:<33} {marca_m:<11} |  {marca_s}")
    if malo != G.FAIL or sano != G.PASS:
        ok = False

# ── 101: token COMPARTIDO con el canonico (necesita filesystem falso) ──
tmp = Path(tempfile.mkdtemp()); (tmp/"messages").mkdir()
real_repo = G.REPO
try:
    G.REPO = tmp
    inst = tmp/"messages"/".agent_session_token_NEXUS-u103"
    canon = tmp/"messages"/".agent_session_token_NEXUS"
    # peligroso: el MISMO contenido que el canonico
    inst.write_text("MISMO-SECRETO"); canon.write_text("MISMO-SECRETO")
    inst.chmod(0o600); canon.chmod(0o600)
    malo = G.brazo_p1_token_por_instancia(cfg(), SANO).veredicto
    # sano: contenido propio
    inst.write_text("SECRETO-PROPIO-DE-LA-INSTANCIA"); inst.chmod(0o600)
    sano = G.brazo_p1_token_por_instancia(cfg(), SANO).veredicto
    print(f"  {'101 token COMPARTIDO con canonico':<33} "
          f"{('FALLA ✓' if malo==G.FAIL else malo+' ✗'):<11} |  "
          f"{'PASS ✓' if sano==G.PASS else sano+' ✗'}")
    if malo != G.FAIL or sano != G.PASS: ok = False
    # permisos flojos
    inst.chmod(0o644)
    perm = G.brazo_p1_token_por_instancia(cfg(), SANO).veredicto
    inst.chmod(0o600)
    perm_ok = G.brazo_p1_token_por_instancia(cfg(), SANO).veredicto
    print(f"  {'101 token con permisos 644':<33} "
          f"{('FALLA ✓' if perm==G.FAIL else perm+' ✗'):<11} |  "
          f"{'PASS ✓' if perm_ok==G.PASS else perm_ok+' ✗'}")
    if perm != G.FAIL or perm_ok != G.PASS: ok = False
finally:
    G.REPO = real_repo

print("\n  " + ("TODOS los brazos DENIEGAN el caso peligroso y APRUEBAN el sano"
                if ok else "HAY BRAZOS QUE NO DISCRIMINAN — revisar"))
sys.exit(0 if ok else 1)
