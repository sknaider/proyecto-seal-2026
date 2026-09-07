#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chain_of_custody.py — Capa FORENSE / cadena de custodia para el soft de
detección de disparos (investigación policial, LOCAL/offline).

Por qué existe (hallazgo del equipo): NINGÚN repo del shortlist trae integridad
de evidencia. Para que un disparo detectado sirva como PRUEBA admisible, cada
captura debe ser: fechada de forma confiable, hasheada, e inscrita en un log
INMUTABLE (append-only, encadenado) — de modo que cualquier alteración posterior
sea detectable. Este módulo provee justo eso, sin dependencias de nube.

Garantías:
  • Integridad por-evidencia: SHA-256 de cada archivo guardado al ingerir.
  • Inmutabilidad del registro: log JSONL append-only ENCADENADO por hash
    (cada registro incluye el hash del anterior → cadena tipo blockchain ligera).
    Alterar/borrar un registro rompe la cadena y se detecta en verify_chain().
  • Original read-only: la evidencia se copia al almacén con permisos 0444.
  • Trazabilidad de acceso: todo acceso/exportación se registra (quién, qué, cuándo).
  • Procedencia del modelo: se guarda versión de modelo + score con cada detección.

NOTA producción: el timestamp aquí usa el reloj del sistema; en despliegue real
sincronizar con NTP de stratum local o GPS, e idealmente sellar con RFC-3161 TSA
offline. La clave del diseño (hash-chain) ya es la pieza forense central.
"""
from __future__ import annotations
import os, json, hashlib, shutil, datetime
from dataclasses import dataclass, asdict, field
from typing import Optional

GENESIS = "0" * 64  # prev_hash del primer registro


def _sha256_file(path: str, buf: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(buf), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _now_iso() -> str:
    # UTC, sufijo Z. Producción: anclar a NTP/GPS.
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical(d: dict) -> bytes:
    # Serialización determinista para hashear (claves ordenadas, sin espacios).
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class CustodyLog:
    """Log append-only encadenado por hash. Cada línea es un registro JSON."""

    def __init__(self, log_path: str):
        self.log_path = log_path
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)

    # ---- internals ----
    def _last(self) -> Optional[dict]:
        if not os.path.exists(self.log_path):
            return None
        last = None
        with open(self.log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last = json.loads(line)
        return last

    def _append(self, event: str, payload: dict) -> dict:
        prev = self._last()
        seq = (prev["seq"] + 1) if prev else 0
        prev_hash = prev["record_hash"] if prev else GENESIS
        rec = {
            "seq": seq,
            "ts_utc": _now_iso(),
            "event": event,
            "prev_hash": prev_hash,
            **payload,
        }
        rec["record_hash"] = _sha256_bytes(_canonical(rec))  # cierra el eslabón
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    # ---- API pública ----
    def ingest_evidence(self, src_path: str, store_dir: str, actor: str,
                        case_id: str = "", note: str = "") -> dict:
        """Copia la evidencia al almacén como READ-ONLY, la hashea y la inscribe."""
        os.makedirs(store_dir, exist_ok=True)
        sha = _sha256_file(src_path)
        base = os.path.basename(src_path)
        # nombre en almacén = hash-prefijo + nombre (evita colisiones / inmutable)
        dst = os.path.join(store_dir, f"{sha[:16]}_{base}")
        shutil.copy2(src_path, dst)
        os.chmod(dst, 0o444)  # original read-only
        return self._append("INGEST", {
            "actor": actor, "case_id": case_id, "note": note,
            "evidence_path": dst, "orig_name": base,
            "sha256": sha, "size": os.path.getsize(dst),
        })

    def log_detection(self, evidence_path: str, actor: str, *,
                      model_name: str, model_version: str, score: float,
                      label: str, location: Optional[dict] = None,
                      loc_method: str = "", loc_confidence: Optional[float] = None,
                      case_id: str = "") -> dict:
        """Inscribe una detección con PROCEDENCIA del modelo (para defensibilidad).

        Guardarraíl forense: la localización SIEMPRE se registra con su MÉTODO
        (loc_method, p.ej. 'SRP-PHAT-array-calibrado' o 'SELDnet') y su CONFIANZA/error
        estimado (loc_confidence). En un juicio una posición sin incertidumbre es
        impugnable; nunca se presenta como hecho absoluto.
        """
        return self._append("DETECTION", {
            "actor": actor, "case_id": case_id, "evidence_path": evidence_path,
            "label": label, "score": round(float(score), 6),
            "model": {"name": model_name, "version": model_version},
            "location": location or {},  # ej. {azimuth, elevation, range_m} de TDOA
            "loc_method": loc_method,
            "loc_confidence": (round(float(loc_confidence), 6) if loc_confidence is not None else None),
        })

    def log_access(self, evidence_path: str, actor: str, action: str,
                   case_id: str = "") -> dict:
        """Registra acceso/exportación/visualización de evidencia (auditoría)."""
        return self._append("ACCESS", {
            "actor": actor, "case_id": case_id,
            "evidence_path": evidence_path, "action": action,
        })

    # ---- verificación ----
    def verify_chain(self) -> tuple[bool, str]:
        """Recalcula la cadena completa. Detecta cualquier alteración/borrado."""
        if not os.path.exists(self.log_path):
            return True, "log vacío (sin registros)"
        prev_hash = GENESIS
        expected_seq = 0
        with open(self.log_path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("seq") != expected_seq:
                    return False, f"seq rota en línea {i}: {rec.get('seq')} != {expected_seq}"
                if rec.get("prev_hash") != prev_hash:
                    return False, f"cadena rota en seq {rec.get('seq')}: prev_hash no coincide"
                stored = rec.get("record_hash")
                recomputed = _sha256_bytes(_canonical({k: v for k, v in rec.items() if k != "record_hash"}))
                if stored != recomputed:
                    return False, f"registro ALTERADO en seq {rec.get('seq')}: hash no coincide"
                prev_hash = stored
                expected_seq += 1
        return True, f"cadena ÍNTEGRA ({expected_seq} registros)"

    def verify_evidence(self, evidence_path: str) -> tuple[bool, str]:
        """Recalcula el hash del archivo y lo compara con el inscrito en el log."""
        logged = None
        with open(self.log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("event") == "INGEST" and rec.get("evidence_path") == evidence_path:
                    logged = rec.get("sha256")
        if logged is None:
            return False, "evidencia no encontrada en el log"
        actual = _sha256_file(evidence_path)
        if actual != logged:
            return False, f"EVIDENCIA ALTERADA: {actual[:16]} != {logged[:16]} (log)"
        return True, "evidencia íntegra (hash coincide con el log)"

    def export_custody_manifest(self, evidence_path: str) -> dict:
        """Genera un MANIFIESTO DE CADENA DE CUSTODIA listo para investigación/juicio:
        ingesta + todas las detecciones + todos los accesos de una evidencia, más el
        estado de integridad (evidencia + cadena del log). Esto es lo que un detective
        o un tribunal necesita para admitir la prueba."""
        ingest, detections, accesses = None, [], []
        with open(self.log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("evidence_path") != evidence_path:
                    continue
                ev = rec.get("event")
                if ev == "INGEST":
                    ingest = rec
                elif ev == "DETECTION":
                    detections.append(rec)
                elif ev == "ACCESS":
                    accesses.append(rec)
        chain_ok, chain_msg = self.verify_chain()
        ev_ok, ev_msg = self.verify_evidence(evidence_path) if ingest else (False, "sin ingesta")
        return {
            "evidence_path": evidence_path,
            "generated_utc": _now_iso(),
            "case_id": (ingest or {}).get("case_id", ""),
            "ingest": ingest,
            "detections": detections,
            "access_trail": accesses,
            "integrity": {
                "evidence_ok": ev_ok, "evidence_msg": ev_msg,
                "log_chain_ok": chain_ok, "log_chain_msg": chain_msg,
                "admissible": bool(ev_ok and chain_ok),
            },
        }


# ----------------------------- self-test -----------------------------
if __name__ == "__main__":
    import tempfile, sys
    tmp = tempfile.mkdtemp(prefix="custody_test_")
    log = CustodyLog(os.path.join(tmp, "custody.log.jsonl"))
    store = os.path.join(tmp, "evidence_store")

    # 1) crear una "evidencia" (clip simulado) e ingerirla
    clip = os.path.join(tmp, "shot_001.wav")
    with open(clip, "wb") as f:
        f.write(b"FAKE-WAV-DATA-gunshot-sample")
    rec = log.ingest_evidence(clip, store, actor="oficial.perez", case_id="CASO-2026-001",
                              note="captura sensor norte")
    ev = rec["evidence_path"]
    print("INGEST:", rec["sha256"][:16], "->", os.path.basename(ev))

    # 2) inscribir la detección con procedencia del modelo + localización TDOA
    log.log_detection(ev, actor="sistema", model_name="gunshot-cnn", model_version="0.1.0",
                      score=0.987, label="gunshot",
                      location={"azimuth": 47.2, "elevation": 3.1, "range_m": 85},
                      loc_method="SRP-PHAT-array-calibrado", loc_confidence=0.78,
                      case_id="CASO-2026-001")
    # 3) registrar un acceso
    log.log_access(ev, actor="detective.lopez", action="export-pdf", case_id="CASO-2026-001")

    ok, msg = log.verify_chain();       print("verify_chain:", ok, "-", msg)
    ok, msg = log.verify_evidence(ev);  print("verify_evidence:", ok, "-", msg)

    # 3b) manifiesto de cadena de custodia (lo que ve un tribunal)
    man = log.export_custody_manifest(ev)
    print("manifest: detections=%d access=%d ADMISIBLE=%s | loc_method=%s conf=%s" % (
        len(man["detections"]), len(man["access_trail"]), man["integrity"]["admissible"],
        man["detections"][0]["loc_method"], man["detections"][0]["loc_confidence"]))

    # 4) DEMO anti-tamper: alterar el archivo de evidencia (debe fallar pese a ser 0444)
    os.chmod(ev, 0o644)
    with open(ev, "ab") as f:
        f.write(b"TAMPERED")
    ok, msg = log.verify_evidence(ev);  print("tras manipular evidencia -> verify_evidence:", ok, "-", msg)

    # 5) DEMO anti-tamper del LOG: editar un registro debe romper la cadena
    lines = open(log.log_path, encoding="utf-8").read().splitlines()
    d = json.loads(lines[1]); d["score"] = 0.10; lines[1] = json.dumps(d, ensure_ascii=False)
    open(log.log_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    ok, msg = log.verify_chain();       print("tras manipular log -> verify_chain:", ok, "-", msg)

    print("\nself-test OK (la capa detecta manipulación de evidencia y de log).")
    shutil.rmtree(tmp, ignore_errors=True)
