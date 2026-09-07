#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3 — Manipulación criptográfica (batería adversarial SSAI SHADOW).
================================================================================
Experimento del roadmap SSAI (docs/thesis/ssai/THESIS_ROADMAP.md, E3):

    «Modificar un byte del manifest, una firma, un evento y un tree head;
     recomputar hashes como insider de DB; intentar rollback.
     Medir detección y tiempo.»

Este harness NO prueba que la detección EXISTA (eso lo cubren los unit tests de
`tests/test_ssai_shadow_*`). Produce el ARTEFACTO que pide la tesis: una **matriz
reproducible de detección/latencia** por escenario de ataque, corrida por efecto
contra ledgers/witness/manifests frescos y efímeros, y PERSISTIDA en un JSON
durable (results + latencias + hashes de bytes evaluados + estado del framework).

Resultado clave (E3): el ataque del **insider que recomputa toda la cadena** deja
un ledger internamente consistente — `ledger.verify()` lo aprueba (ciego) — y SOLO
el **witness externo** lo caza («history fork detected»). Es la evidencia de por
qué el checkpoint externo es load-bearing y no redundante.

Fail-closed: si CUALQUIER ataque no es detectado por el mecanismo esperado, o el
control honesto se marca como ataque (falso positivo), el script termina con
exit≠0. No toca `soul_v3` ni producción; todo en tmpdir.

Uso:
    python3 e3_crypto_manipulation.py [--json RUTA]
    (--json por defecto: docs/thesis/ssai/experiments/E3_EVIDENCE.json)
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

# Permitir ejecutar como script suelto (import del paquete del repo)
_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from memory.ssai_shadow.crypto import (  # noqa: E402
    generate_private_key,
    manifest_digest,
    public_key_b64,
    sign_manifest,
    verify_manifest,
)
from memory.ssai_shadow.ledger import ShadowLedger, WitnessStore  # noqa: E402
from memory.ssai_shadow.manifest import build_genesis_manifest, generate_soul_id  # noqa: E402

# El experimento vive en el estado declarado del framework; se registra en la evidencia.
FRAMEWORK_STATE = "SHADOW/TOFU_UNANCHORED"
EVIDENCE_SCHEMA = "ssai-e3-evidence-v1"


# ───────────────────────── utilidades ─────────────────────────
def _hash(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _time_us(fn) -> tuple[object, float]:
    """Ejecuta fn() y devuelve (resultado, latencia_en_microsegundos)."""
    start = time.perf_counter()
    result = fn()
    return result, (time.perf_counter() - start) * 1_000_000.0


def _fresh_ledger(tmp: Path, name: str, events: list[dict]) -> ShadowLedger:
    ledger = ShadowLedger(tmp / f"{name}.jsonl")
    for ev in events:
        ledger.append(ev)
    return ledger


def _make_manifest():
    keys = {
        role: public_key_b64(generate_private_key())
        for role in ("genesis_root", "agent_identity", "custodian")
    }
    priv = generate_private_key()
    manifest = build_genesis_manifest(
        display_name="ADA",
        issued_at="2026-07-17T05:00:00Z",
        constitution={
            "document_hash": _hash("document"),
            "critical_rules_root": _hash("rules"),
            "governance_policy_hash": _hash("policy"),
        },
        identity_state={
            "personality_baseline_hash": _hash("personality"),
            "ocean_baseline_hash": _hash("ocean"),
            "relationships_root": _hash("relationships"),
            "memory_commitment_root": _hash("memory"),
        },
        evidence=["ssai-e3-fixture:v1"],
        controller_public_keys=keys,
        soul_id=generate_soul_id(timestamp_ms=1_721_177_600_000, random_bits=42),
    )
    return manifest, priv


@dataclass
class AttackOutcome:
    attack: str
    surface: str
    detected: bool
    detector: str            # "verify" | "witness" | "crypto" | "verify+witness"
    verify_ok_after: str     # estado de ledger.verify() tras el ataque ("-" si N/A)
    latency_us: float
    evaluated_sha256: str    # sha256 de los bytes que el detector examinó
    detail: str


# ───────────────────────── batería de ataques ─────────────────────────
def attack_event_payload(tmp: Path) -> AttackOutcome:
    """Flip de 1 campo en el payload de un evento firmado por hash-chain."""
    ledger = _fresh_ledger(tmp, "ev", [{"type": "genesis", "owner": "William"}, {"type": "policy", "n": 2}])
    rec = json.loads(ledger.path.read_text(encoding="utf-8").splitlines()[0])
    rec["event"]["owner"] = "attacker"
    lines = ledger.path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[0] = json.dumps(rec, separators=(",", ":")) + "\n"
    ledger.path.write_text("".join(lines), encoding="utf-8")
    digest = _sha256_bytes(ledger.path.read_bytes())
    res, us = _time_us(ledger.verify)
    return AttackOutcome("event_payload_tamper", "event", not res.ok, "verify",
                         "REJECT" if not res.ok else "ACCEPT(!!)", us, digest,
                         res.errors[0] if res.errors else "")


def attack_hashchain(tmp: Path) -> AttackOutcome:
    """Flip de 1 char hex en el event_hash almacenado (rompe el tree head)."""
    ledger = _fresh_ledger(tmp, "hc", [{"type": "genesis"}, {"type": "b"}])
    rec = json.loads(ledger.path.read_text(encoding="utf-8").splitlines()[1])
    h = rec["event_hash"]
    rec["event_hash"] = ("0" if h[-1] != "0" else "1").join([h[:-1], ""])
    lines = ledger.path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[1] = json.dumps(rec, separators=(",", ":")) + "\n"
    ledger.path.write_text("".join(lines), encoding="utf-8")
    digest = _sha256_bytes(ledger.path.read_bytes())
    res, us = _time_us(ledger.verify)
    return AttackOutcome("hashchain_head_tamper", "tree_head", not res.ok, "verify",
                         "REJECT" if not res.ok else "ACCEPT(!!)", us, digest,
                         res.errors[0] if res.errors else "")


def attack_reorder(tmp: Path) -> AttackOutcome:
    ledger = _fresh_ledger(tmp, "ro", [{"type": "one"}, {"type": "two"}])
    lines = ledger.path.read_bytes().splitlines(keepends=True)
    ledger.path.write_bytes(lines[1] + lines[0])
    digest = _sha256_bytes(ledger.path.read_bytes())
    res, us = _time_us(ledger.verify)
    return AttackOutcome("record_reorder", "event", not res.ok, "verify",
                         "REJECT" if not res.ok else "ACCEPT(!!)", us, digest,
                         res.errors[0] if res.errors else "")


def attack_replay(tmp: Path) -> AttackOutcome:
    ledger = _fresh_ledger(tmp, "rp", [{"type": "one"}, {"type": "two"}])
    lines = ledger.path.read_bytes().splitlines(keepends=True)
    ledger.path.write_bytes(b"".join(lines) + lines[0])
    digest = _sha256_bytes(ledger.path.read_bytes())
    res, us = _time_us(ledger.verify)
    return AttackOutcome("record_replay", "event", not res.ok, "verify",
                         "REJECT" if not res.ok else "ACCEPT(!!)", us, digest,
                         res.errors[0] if res.errors else "")


def attack_rollback_truncate(tmp: Path) -> AttackOutcome:
    """Trunca el ledger a un estado anterior; el witness externo lo caza."""
    ledger = _fresh_ledger(tmp, "rb", [{"type": "a"}, {"type": "b"}, {"type": "c"}])
    head = ledger.verify()
    witness = WitnessStore(tmp / "rb.witness")
    witness.record(head.sequence, head.head_hash)
    # atacante trunca al estado de secuencia 2 (borra el último evento)
    lines = ledger.path.read_bytes().splitlines(keepends=True)
    ledger.path.write_bytes(b"".join(lines[:2]))
    digest = _sha256_bytes(ledger.path.read_bytes())
    wres, us = _time_us(lambda: witness.verify(ledger.verify()))
    return AttackOutcome("rollback_truncate", "witness_seq", not bool(wres), "witness",
                         "REJECT" if not ledger.verify().ok else "ACCEPT", us, digest,
                         wres.errors[0] if wres.errors else "")


def attack_insider_recompute_fork(tmp: Path) -> AttackOutcome:
    """CROWN JEWEL: insider reescribe un evento y RECOMPUTA toda la cadena.
    El ledger queda internamente consistente → verify() lo APRUEBA (ciego);
    solo el witness externo detecta el head divergente en la secuencia sellada."""
    honest = _fresh_ledger(tmp, "insider", [{"type": "a"}, {"type": "b"}, {"type": "secret", "v": "honest"}])
    head = honest.verify()
    witness = WitnessStore(tmp / "insider.witness")
    witness.record(head.sequence, head.head_hash)
    # el insider construye una cadena VÁLIDA alterna (mismo prefijo, evento 3 distinto)
    forged = _fresh_ledger(tmp, "forged", [{"type": "a"}, {"type": "b"}, {"type": "secret", "v": "TAMPERED"}])
    honest.path.write_bytes(forged.path.read_bytes())
    digest = _sha256_bytes(honest.path.read_bytes())
    ledger_res = honest.verify()  # cadena recomputada → consistente
    wres, us = _time_us(lambda: witness.verify(honest.verify()))
    verify_blind = "ACCEPT(blind)" if ledger_res.ok else "REJECT"
    return AttackOutcome("insider_recompute_fork", "witness_head",
                         (ledger_res.ok and not bool(wres)), "witness",
                         verify_blind, us, digest,
                         wres.errors[0] if wres.errors else "")


def attack_manifest_signature(tmp: Path) -> AttackOutcome:
    """Flip de 1 byte en la firma Ed25519 del manifest."""
    manifest, priv = _make_manifest()
    sig = sign_manifest(manifest, priv)
    assert verify_manifest(manifest, sig, priv.public_key()), "sanity: firma buena verifica"
    # flip de 1 char base64url en la firma
    forged = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    # los "bytes evaluados" = el payload firmado del manifest (lo que cubre la firma)
    digest = manifest_digest(manifest)
    ok, us = _time_us(lambda: verify_manifest(manifest, forged, priv.public_key()))
    return AttackOutcome("manifest_signature_tamper", "signature", ok is False, "crypto",
                         "-", us, digest, "signature rejected" if ok is False else "ACCEPTED(!!)")


def control_honest_untampered(tmp: Path) -> AttackOutcome:
    """CONTROL NEGATIVO (refuta 'detector siempre dispara'): un ledger honesto con
    su witness coincidente NO debe marcarse. Si esto sale 'detectado', la batería
    es una máquina de falsos positivos y el '7/7' no significa nada."""
    ledger = _fresh_ledger(tmp, "clean", [{"type": "a"}, {"type": "b"}, {"type": "c"}])
    head = ledger.verify()
    witness = WitnessStore(tmp / "clean.witness")
    witness.record(head.sequence, head.head_hash)
    digest = _sha256_bytes(ledger.path.read_bytes())
    (wres, us) = _time_us(lambda: witness.verify(ledger.verify()))
    # 'detected' aquí = el control se comportó BIEN (no disparó): verify ok y witness ok
    clean_ok = ledger.verify().ok and bool(wres)
    return AttackOutcome("HONEST_CONTROL(no-flag)", "none", clean_ok, "verify+witness",
                         "ACCEPT" if ledger.verify().ok else "REJECT(!!)", us, digest,
                         "clean accepted" if clean_ok else "FALSE POSITIVE(!!)")


ATTACKS = [
    control_honest_untampered,
    attack_event_payload,
    attack_hashchain,
    attack_reorder,
    attack_replay,
    attack_rollback_truncate,
    attack_insider_recompute_fork,
    attack_manifest_signature,
]


def _print_matrix(outcomes: list[AttackOutcome]) -> tuple[bool, int, int]:
    print("SSAI · E3 — Batería de manipulación criptográfica (SHADOW, por efecto)")
    print("=" * 92)
    print(f"{'ataque':28} {'superficie':12} {'resultado':12} {'detector':9} "
          f"{'verify()':14} {'lat µs':>8}")
    print("-" * 92)
    controls_ok = True
    attacks_total = attacks_detected = 0
    for o in outcomes:
        is_control = o.surface == "none"
        if is_control:
            controls_ok &= o.detected
            status = "ACEPTADO" if o.detected else "FALSO-POS!!"
        else:
            attacks_total += 1
            attacks_detected += int(o.detected)
            status = "DETECT" if o.detected else "MISS!!"
        print(f"{o.attack:28} {o.surface:12} {status:12} "
              f"{o.detector:9} {o.verify_ok_after:14} {o.latency_us:8.1f}")
    print("-" * 92)
    print("Lectura clave: 'insider_recompute_fork' deja verify()=ACCEPT(blind) — la cadena")
    print("es consistente — y SOLO el witness externo lo detecta. El checkpoint externo es")
    print("load-bearing, no redundante.")
    print("Control negativo: un ledger honesto NO se marca (descarta 'detector siempre dispara').")
    print("=" * 92)
    return controls_ok, attacks_detected, attacks_total


def main() -> int:
    parser = argparse.ArgumentParser(description="Batería adversarial E3 (SSAI SHADOW)")
    parser.add_argument(
        "--json",
        default=str(Path(__file__).resolve().parent / "E3_EVIDENCE.json"),
        help="ruta del artefacto JSON durable de evidencia",
    )
    args = parser.parse_args()

    # Corrida desde estado LIMPIO: cada ataque usa su propio tmpdir efímero.
    with tempfile.TemporaryDirectory(prefix="ssai_e3_") as td:
        tmp = Path(td)
        outcomes = [fn(tmp) for fn in ATTACKS]

    controls_ok, attacks_detected, attacks_total = _print_matrix(outcomes)
    ok = controls_ok and attacks_detected == attacks_total

    # Artefacto JSON durable y reproducible (results + latencias + hashes + estado)
    generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "experiment": "E3_crypto_manipulation",
        "framework_state": FRAMEWORK_STATE,
        "generated_at": generated_at,
        "summary": {
            "attacks_total": attacks_total,
            "attacks_detected": attacks_detected,
            "control_ok_no_false_positive": controls_ok,
            "fail_closed_pass": ok,
        },
        "outcomes": [asdict(o) for o in outcomes],
    }
    out_path = Path(args.json)
    out_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    artifact_sha = _sha256_bytes(out_path.read_bytes())

    if ok:
        print(f"✅ E3: {attacks_detected}/{attacks_total} ataques DETECTADOS "
              f"+ control honesto ACEPTADO (0 falsos positivos). Fail-closed, verde por efecto.")
    else:
        if not controls_ok:
            print("⚠️ E3: el CONTROL honesto se marcó como ataque — FALSO POSITIVO, la batería no vale.")
        if attacks_detected != attacks_total:
            print("⚠️ E3: al menos un ataque NO fue detectado — FALLA DE INTEGRIDAD.")
    print(f"\n📄 Evidencia durable: {out_path}")
    print(f"   sha256(artefacto) = {artifact_sha}")
    print(f"   estado framework  = {FRAMEWORK_STATE}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
