#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
detect_pipeline.py — PIPELINE FORENSE INTEGRADO (JARVIS, director del proyecto).
================================================================================
Une las 3 piezas del soft policial en UN solo flujo, sobre audio REAL:

  audio → [1 DETECCIÓN] clasificador v2 (¿disparo? + confianza)
        → [2 LOCALIZACIÓN] GCC-PHAT/TDOA si hay multicanal (¿de dónde?, con MÉTODO+confianza)
        → [3 CADENA DE CUSTODIA] ingesta read-only + SHA-256 + log encadenado + manifiesto

Honestidad (guardarraíles del equipo):
  • La detección usa el modelo v2; su generalización a un 3er dominio NO está probada aún
    (ver metrics_v2.json) → la confianza se reporta, no se absolutiza.
  • La localización GCC-PHAT funciona con array CALIBRADO y poca reverb; sin geometría
    calibrada el ángulo es ILUSTRATIVO → se inscribe SIEMPRE con loc_method+loc_confidence
    (una posición sin incertidumbre es impugnable en juicio — guardarraíl forense de NEXUS).

Uso:  python detect_pipeline.py <audio.wav> [audio2.wav ...] [--case CASO-2026-001]
      multicanal: pasar los canales del MISMO evento juntos → intenta DoA.
"""
from __future__ import annotations
import os, sys, json, glob, argparse
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "forensic"))

from gunshot_train import features_from_wave, load_wav, SR   # detección
import gunshot_localization_poc as loc                       # localización
from chain_of_custody import CustodyLog                      # forense

MODEL_PATH = os.path.join(HERE, "artifacts", "gunshot_detector_v2.joblib")
MODEL_VERSION = "v2_cross_dataset"
STORE = os.path.join(HERE, "..", "forensic", "evidence_store")
LOG = os.path.join(HERE, "..", "forensic", "custody.log.jsonl")
MIC_DIST_DEFAULT = 0.30   # m, separación asumida entre canales (ILUSTRATIVO sin calibración)


def _load_model():
    import joblib
    if not os.path.exists(MODEL_PATH):
        sys.exit(f"❌ falta el modelo {MODEL_PATH} — corré gunshot_train_v2.py primero")
    return joblib.load(MODEL_PATH)


def detect(model, wav_path):
    """Devuelve (label, score) para un archivo. score = prob. de disparo."""
    wave = load_wav(wav_path, do_denoise=True)
    feat = features_from_wave(wave).reshape(1, -1)
    proba = model.predict_proba(feat)[0]
    classes = list(model.classes_)
    p_gun = float(proba[classes.index(1)]) if 1 in classes else float(proba[-1])
    return ("gunshot" if p_gun >= 0.5 else "no_gunshot"), p_gun


def try_localize(wav_paths, mic_dist=MIC_DIST_DEFAULT):
    """Si hay 2+ canales del MISMO evento, estima DoA (2 mic) o posición 2D (4 mic).
    Devuelve (location_dict, method, confidence) o (None, '', None) si no aplica."""
    if len(wav_paths) < 2:
        return None, "", None
    import librosa
    sigs, fss = [], []
    for p in wav_paths:
        s, fs = librosa.load(p, sr=None, mono=True)
        sigs.append(s); fss.append(fs)
    fs = fss[0]
    L = min(len(s) for s in sigs)
    sigs = [s[:L] for s in sigs]
    try:
        if len(sigs) >= 4:
            x, y, err = loc.localize_2d(sigs[:4], [(0, 0), (mic_dist, 0),
                                        (0, mic_dist), (mic_dist, mic_dist)], fs)
            conf = float(max(0.0, 1.0 - min(err * fs, 1.0)))   # error TDOA→confianza cruda
            return ({"x_m": round(float(x), 2), "y_m": round(float(y), 2), "tdoa_rms_s": round(float(err), 6)},
                    "multilateracion-TDOA-grid (array NO calibrado, ILUSTRATIVO)", round(conf, 3))
        deg, tau = loc.doa_2mic(sigs[1], sigs[0], fs, mic_dist)
        return ({"azimuth_deg": round(float(deg), 1), "tdoa_s": round(float(tau), 6)},
                "GCC-PHAT-DoA-2mic (separacion asumida, ILUSTRATIVO sin calibrar)", 0.4)
    except Exception as e:
        return None, f"localizacion fallo: {e}", None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", nargs="+", help="archivo(s) .wav; multicanal del mismo evento juntos")
    ap.add_argument("--case", default="CASO-DEMO-001")
    ap.add_argument("--actor", default="sistema-seal")
    args = ap.parse_args()

    model = _load_model()
    cust = CustodyLog(LOG)
    print(f"🔬 PIPELINE FORENSE — caso {args.case} | modelo {MODEL_VERSION}\n")

    # Detección sobre el primer canal (o cada archivo si son eventos distintos)
    primary = args.audio[0]
    label, score = detect(model, primary)
    print(f"[1] DETECCIÓN: {os.path.basename(primary)} → {label.upper()}  (confianza disparo={score:.3f})")

    location, method, conf = try_localize(args.audio)
    if location:
        print(f"[2] LOCALIZACIÓN: {location}  | método={method} conf={conf}")
    else:
        print(f"[2] LOCALIZACIÓN: no aplica ({'mono — 1 canal' if len(args.audio)<2 else method})")

    # Cadena de custodia: ingesta + detección inscrita + manifiesto
    rec = cust.ingest_evidence(primary, STORE, actor=args.actor, case_id=args.case,
                               note="ingesta automática pipeline forense")
    ev = rec["evidence_path"]
    cust.log_detection(ev, actor=args.actor, model_name="gunshot-rf", model_version=MODEL_VERSION,
                       score=score, label=label, location=location or {},
                       loc_method=method, loc_confidence=conf, case_id=args.case)
    manifest = cust.export_custody_manifest(ev)
    print(f"[3] CUSTODIA: evidencia hasheada (SHA-256 {rec['sha256'][:16]}…), inscrita en cadena.")
    print(f"    integridad: evidencia_ok={manifest['integrity']['evidence_ok']} "
          f"cadena_ok={manifest['integrity']['log_chain_ok']} "
          f"ADMISIBLE={manifest['integrity']['admissible']}")

    out = os.path.join(HERE, "artifacts", f"forensic_report_{args.case}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"\n✅ reporte forense → {out}")


if __name__ == "__main__":
    main()
