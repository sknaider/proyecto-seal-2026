#!/usr/bin/env python3
"""Convocatoria automática del FABLE JUEZ (William 3-sep-2026 18:31: «sí, hazlo cada hora que cheque»).

Cada hora (timer fable-juez-convocatoria.timer) busca manifiestos de calidad con
`independent_reviewer: FABLE` y `review.status != approved`, y le deja el caso en su
canal `fable-juez` UNA sola vez por versión del manifiesto (idempotente por sha256
del archivo; si el dueño cambia el manifiesto, se vuelve a convocar).

El juez NO se autoconvoca: lo convoca el sistema con un caso concreto. Este script
no firma, no cambia estados, no toca el repo: sólo escribe en el canal.

    python3 fable/fable_juez_convocatoria.py            # convoca lo pendiente
    python3 fable/fable_juez_convocatoria.py --dry-run  # muestra sin enviar
"""
from __future__ import annotations

import argparse, glob, hashlib, json, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "quality" / "manifests"
STATE = ROOT / "fable" / ".juez_convocatorias.json"
SEND = ROOT / "scripts" / "seal_send.py"
CHANNEL = "fable-juez"
SENDER = "ADA"          # dueño del chequeo; el mensaje dice que es automático


def motivo_de_convocatoria(m: dict) -> str | None:
    """Por qué habría que convocar al juez para este manifiesto — o None.

    ARREGLO 8-sep-2026 (NEXUS, orden de William: «cablea y repara las conexiones
    de fable»). El selector anterior pedía DOS condiciones a la vez:

        independent_reviewer == FABLE   Y   review.status != approved

    y con eso **nunca podía convocar para juzgar**, porque un carril listo para
    el juez es exactamente el contrario: tiene OTRO revisor (ALICE, JARVIS,
    NEXUS) y **ya está** `approved`. Medido ese día: 0 manifiestos de 93
    cumplían el filtro, así que el timer corría puntual cada hora y no mandaba
    nada, mientras William reclamaba tres veredictos.

    Son DOS trabajos distintos y el mensaje debe decir cuál es:

        revision  -> FABLE es el revisor independiente y todavía no firmó
        juicio    -> otro ya firmó; falta el veredicto del juez
                     («todo pasa por el juez», William 7-sep)
    """
    revisor = str(m.get("independent_reviewer", "")).strip().upper()
    estado = str((m.get("review") or {}).get("status", "")).strip().lower()
    if revisor == "FABLE":
        return None if estado == "approved" else "revision"
    return "juicio" if estado == "approved" else None


def pending_cases(manifests_dir: Path = MANIFESTS) -> list[dict]:
    out = []
    for p in sorted(glob.glob(str(manifests_dir / "*.json"))):
        try:
            m = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        motivo = motivo_de_convocatoria(m)
        if motivo is None:
            continue
        rel = str(Path(p).resolve().relative_to(ROOT.resolve())) if str(p).startswith(str(ROOT)) else p
        out.append({
            "path": rel,
            "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest(),
            "owner": m.get("owner", "?"),
            "change_id": m.get("change_id", Path(p).stem),
            "subjects": list(m.get("subjects", [])),
            "tests": list(m.get("tests", [])),
            "status": (m.get("review") or {}).get("status", "?"),
            "motivo": motivo,
            "revisor": m.get("independent_reviewer", "?"),
        })
    return out


def load_state(path: Path = STATE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict, path: Path = STATE) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def render(case: dict) -> str:
    subj = "\n".join(f"  - `{s}`" for s in case["subjects"]) or "  - (sin sujetos declarados)"
    tests = "\n".join(f"  - `{t}`" for t in case["tests"]) or "  - (sin tests declarados)"
    return (
        f"**Convocatoria automática (chequeo horario, orden de William 3-sep 18:31).**\n\n"
        f"Caso: revisión independiente pendiente del manifiesto `{case['path']}`\n"
        f"- change_id: `{case['change_id']}` · dueño: **{case['owner']}** · estado: `{case['status']}`\n"
        f"- sujetos:\n{subj}\n"
        f"- tests:\n{tests}\n\n"
        f"Se pide: APPROVE o REJECT con recibo (`review.receipt` con `reviewed_sha256` y `manifest_digest`), "
        f"medido por tu mano. Si el expediente no alcanza, pedíselo al dueño en este canal. "
        f"Esta convocatoria no firma ni cambia estados; sólo te deja el caso."
    )


def send(case: dict, dry_run: bool) -> bool:
    key = f"fable-juez-convocatoria-{case['change_id']}-{case['sha256'][:12]}"
    if dry_run:
        print(f"[dry-run] {case['path']} -> {CHANNEL} key={key}")
        return True
    env = dict(os.environ, SEAL_AGENT=SENDER)
    r = subprocess.run(
        [sys.executable, str(SEND), SENDER, "FABLE", render(case), "--channel", CHANNEL,
         "--type", "conversation", "--idempotency-key", key],
        capture_output=True, text=True, timeout=90, env=env, cwd=str(ROOT),
    )
    ok = r.returncode == 0 and '"ok":true' in (r.stdout or "").replace(" ", "")
    print(f"{'OK' if ok else 'FALLO'} {case['path']} rc={r.returncode} {(r.stdout or r.stderr).strip()[-160:]}")
    return ok


MAX_POR_CORRIDA = 3
"""Tope de convocatorias por corrida.

POR QUÉ EXISTE (NEXUS, 8-sep-2026): al arreglar el selector para que también
convocara a JUICIO, el modo seco mostró **69 casos de golpe**. Con el timer
horario eso son 69 mensajes seguidos a FABLE en un minuto: **un flood es peor
que el silencio que veníamos de arreglar** —lo ahoga y le hace perder el caso
importante entre 68 iguales—. Con tope 3 y timer horario la cola se drena en un
día sin ahogar a nadie, y el estado sigue siendo idempotente por sha.

Se descubrió porque se corrió `--dry-run` ANTES de desplegar. Sin esa corrida,
el arreglo del silencio habría entregado un flood.
"""


def run(dry_run: bool = False, manifests_dir: Path = MANIFESTS, state_path: Path = STATE,
        sender=send, max_por_corrida: int = MAX_POR_CORRIDA) -> dict:
    state = load_state(state_path)
    cases = pending_cases(manifests_dir)
    sent, skipped, diferidos = [], [], []
    for c in cases:
        if state.get(c["path"]) == c["sha256"]:
            skipped.append(c["path"]); continue
        if max_por_corrida is not None and len(sent) >= max_por_corrida:
            diferidos.append(c["path"]); continue
        if sender(c, dry_run):
            sent.append(c["path"])
            if not dry_run:
                state[c["path"]] = c["sha256"]
    if not dry_run and sent:
        save_state(state, state_path)
    summary = {"pending": len(cases), "sent": sent, "already_convoked": skipped,
               "diferidos_por_tope": len(diferidos), "tope": max_por_corrida}
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-por-corrida", type=int, default=MAX_POR_CORRIDA,
                    help="0 o menos = sin tope (NO usar en vivo: son 69 casos)")
    a = ap.parse_args()
    # <=0 significa SIN TOPE, y hay que escribirlo: pasar 0 tal cual haría que
    # `len(sent) >= 0` fuera cierto desde el primer caso y no enviaría ninguno,
    # que es el opuesto exacto de lo que pide la bandera.
    tope = a.max_por_corrida if a.max_por_corrida > 0 else None
    run(dry_run=a.dry_run, max_por_corrida=tope)
    return 0


if __name__ == "__main__":
    sys.exit(main())
