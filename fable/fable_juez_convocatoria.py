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


def pending_cases(manifests_dir: Path = MANIFESTS) -> list[dict]:
    out = []
    for p in sorted(glob.glob(str(manifests_dir / "*.json"))):
        try:
            m = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(m.get("independent_reviewer", "")).strip().upper() != "FABLE":
            continue
        if (m.get("review") or {}).get("status") == "approved":
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


def run(dry_run: bool = False, manifests_dir: Path = MANIFESTS, state_path: Path = STATE, sender=send) -> dict:
    state = load_state(state_path)
    cases = pending_cases(manifests_dir)
    sent, skipped = [], []
    for c in cases:
        if state.get(c["path"]) == c["sha256"]:
            skipped.append(c["path"]); continue
        if sender(c, dry_run):
            sent.append(c["path"])
            if not dry_run:
                state[c["path"]] = c["sha256"]
    if not dry_run and sent:
        save_state(state, state_path)
    summary = {"pending": len(cases), "sent": sent, "already_convoked": skipped}
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(dry_run=a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
