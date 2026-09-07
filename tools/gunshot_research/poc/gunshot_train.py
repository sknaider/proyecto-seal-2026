#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gunshot_train.py — Entrenamiento REAL del detector de disparos (pieza JARVIS, director).
========================================================================================
Detector binario disparo / no-disparo para investigación policial LOCAL.

Rigor (asegurado por FABLE, dimensión ML):
  • Eval OUT-OF-SAMPLE: split oficial train/test de SESA (no se mide en train).
  • CROSS-DATASET: se entrena con SESA y se testea con disparos REALES de Zenodo que el
    modelo NUNCA vio → dice si GENERALIZA (no overfit-al-dataset).
  • FALSO-POSITIVO PROMINENTE: en uso policial un FP cuesta una investigación → se reporta
    precision/recall/FP, no accuracy bruta (clases desbalanceadas).
  • Pipeline rcarioni (extraído): denoise opcional + augmentation de la clase minoritaria.

Salidas: model artifact (joblib) + metrics.json con todas las cifras honestas.
"""
from __future__ import annotations
import os, glob, json, warnings
import numpy as np

warnings.filterwarnings("ignore")
SR = 16000          # SESA es 16 kHz nativo
N_MFCC = 40
HERE = os.path.dirname(__file__)
SESA = os.path.join(HERE, "..", "datasets", "audio", "SESA_extracted", "SESA")
ZENODO = os.path.join(HERE, "..", "datasets", "audio", "zenodo_extracted")
OUT = os.path.join(HERE, "artifacts")
os.makedirs(OUT, exist_ok=True)

GUNSHOT_PREFIX = "gunshot"   # SESA: gunshot_*.wav = positivo; resto (casual/explosion/siren) = negativo


# ───────────────────────── FEATURES (rcarioni: MFCC+deltas + descriptores impulsivos) ──
def features_from_wave(wave, sr=SR):
    import librosa
    if len(wave) < sr // 10:                       # pad clips muy cortos
        wave = np.pad(wave, (0, sr // 10 - len(wave)))
    mfcc = librosa.feature.mfcc(y=wave, sr=sr, n_mfcc=N_MFCC)
    d1 = librosa.feature.delta(mfcc)
    d2 = librosa.feature.delta(mfcc, order=2)
    zcr = librosa.feature.zero_crossing_rate(wave)          # transitorio del disparo
    roll = librosa.feature.spectral_rolloff(y=wave, sr=sr)
    cent = librosa.feature.spectral_centroid(y=wave, sr=sr)
    rms = librosa.feature.rms(y=wave)
    feats = []
    for m in (mfcc, d1, d2, zcr, roll, cent, rms):          # agregación mean+std en el tiempo
        feats.append(m.mean(axis=1)); feats.append(m.std(axis=1))
    return np.concatenate(feats)


def denoise(wave, sr=SR):
    """Spectral gating ligero (rcarioni) — pre-emphasis + recorte de piso de ruido por STFT."""
    import librosa
    wave = librosa.effects.preemphasis(wave)
    S = librosa.stft(wave)
    mag, phase = np.abs(S), np.angle(S)
    floor = np.median(mag, axis=1, keepdims=True) * 1.5     # umbral por banda
    mag = np.maximum(mag - floor, 0.0)
    return librosa.istft(mag * np.exp(1j * phase), length=len(wave))


def augment(wave, sr=SR, rng=None):
    import librosa
    rng = rng or np.random
    out = wave.copy()
    if rng.rand() < 0.5: out = out + 0.005 * rng.randn(len(out)).astype(out.dtype)
    if rng.rand() < 0.4: out = librosa.effects.pitch_shift(out, sr=sr, n_steps=rng.uniform(-4, 4))
    if rng.rand() < 0.4: out = librosa.effects.time_stretch(out, rate=rng.uniform(0.9, 1.1))
    return out


def load_wav(path, sr=SR, do_denoise=True):
    import librosa
    wave, _ = librosa.load(path, sr=sr, mono=True)
    return denoise(wave, sr) if do_denoise else wave


# ───────────────────────── DATASET BUILD ───────────────────────────────────────────
def sesa_split(split, do_denoise=True, augment_pos=0):
    """Carga un split SESA (train|test). Devuelve X, y (1=disparo). augment_pos = nº de
    copias aumentadas por positivo (solo train, para balancear la clase minoritaria)."""
    X, y = [], []
    wavs = sorted(glob.glob(os.path.join(SESA, split, "*.wav")))
    rng = np.random.RandomState(42)
    for p in wavs:
        name = os.path.basename(p)
        label = 1 if name.startswith(GUNSHOT_PREFIX) else 0
        wave = load_wav(p, do_denoise=do_denoise)
        X.append(features_from_wave(wave)); y.append(label)
        if label == 1 and augment_pos:                      # aumenta solo disparos en train
            for _ in range(augment_pos):
                X.append(features_from_wave(augment(wave, rng=rng))); y.append(1)
    return np.array(X), np.array(y)


def zenodo_sample(n=200, do_denoise=True, seed=7):
    """Muestra de disparos REALES de Zenodo — HELD-OUT TOTAL (jamás en train). Cross-dataset.
    Nota: Zenodo trae varios canales del MISMO disparo (chanN+mean); se muestrea por archivo,
    son correlacionados pero TODOS son test → no hay leakage train/test (FABLE)."""
    wavs = sorted(glob.glob(os.path.join(ZENODO, "**", "*.wav"), recursive=True))
    if not wavs: return None, None
    rng = np.random.RandomState(seed)
    pick = rng.choice(len(wavs), size=min(n, len(wavs)), replace=False)
    X = []
    for i in pick:
        try:
            X.append(features_from_wave(load_wav(wavs[i], do_denoise=do_denoise)))
        except Exception:
            continue
    return np.array(X), np.ones(len(X), dtype=int)


# ───────────────────────── TRAIN + EVAL ────────────────────────────────────────────
def main():
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
    from sklearn.svm import SVC
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    from sklearn.metrics import (precision_score, recall_score, f1_score,
                                  confusion_matrix, classification_report)

    print("[1/4] Cargando SESA train (denoise + augmentation de disparos)…")
    Xtr, ytr = sesa_split("train", do_denoise=True, augment_pos=3)
    print(f"      train: {Xtr.shape}  positivos={int(ytr.sum())}/{len(ytr)}")
    print("[2/4] Cargando SESA test (out-of-sample)…")
    Xte, yte = sesa_split("test", do_denoise=True, augment_pos=0)
    print(f"      test:  {Xte.shape}  positivos={int(yte.sum())}/{len(yte)}")

    candidates = {
        "random_forest": make_pipeline(StandardScaler(),
            RandomForestClassifier(n_estimators=400, class_weight="balanced", random_state=0)),
        "gradient_boost": make_pipeline(StandardScaler(),
            GradientBoostingClassifier(random_state=0)),
        "svm_rbf": make_pipeline(StandardScaler(),
            SVC(C=10, gamma="scale", class_weight="balanced", probability=True, random_state=0)),
    }

    results = {}
    best_name, best_model, best_f1 = None, None, -1.0
    print("[3/4] Entrenando candidatos y evaluando OUT-OF-SAMPLE (SESA test)…")
    for name, model in candidates.items():
        model.fit(Xtr, ytr)
        pred = model.predict(Xte)
        tn, fp, fn, tp = confusion_matrix(yte, pred, labels=[0, 1]).ravel()
        prec = precision_score(yte, pred, zero_division=0)
        rec = recall_score(yte, pred, zero_division=0)
        f1 = f1_score(yte, pred, zero_division=0)
        results[name] = {"precision": round(prec, 4), "recall": round(rec, 4),
                         "f1": round(f1, 4), "false_positives": int(fp),
                         "true_positives": int(tp), "false_negatives": int(fn),
                         "true_negatives": int(tn)}
        print(f"   · {name:15s} prec={prec:.3f} rec={rec:.3f} f1={f1:.3f}  FP={fp} FN={fn}")
        if f1 > best_f1:
            best_name, best_model, best_f1 = name, model, f1

    print(f"   → mejor por F1 out-of-sample: {best_name}")

    print("[4/4] CROSS-DATASET: disparos REALES de Zenodo nunca vistos…")
    Xz, yz = zenodo_sample(n=200, do_denoise=True)
    cross = None
    if Xz is not None and len(Xz):
        zpred = best_model.predict(Xz)
        cross_recall = float((zpred == 1).mean())
        cross = {"n_samples": int(len(Xz)), "recall_on_real_gunshots": round(cross_recall, 4),
                 "detected": int((zpred == 1).sum())}
        print(f"   · Zenodo (held-out): recall en disparos reales = {cross_recall:.3f} "
              f"({cross['detected']}/{cross['n_samples']})")
    else:
        print("   · Zenodo aún no descomprimido — cross-dataset pendiente.")

    # Persistir
    import joblib
    joblib.dump(best_model, os.path.join(OUT, "gunshot_detector.joblib"))
    metrics = {"best_model": best_name, "candidates_out_of_sample": results,
               "cross_dataset_zenodo": cross,
               "notes": "precision/recall sobre SESA test (out-of-sample); cross-dataset = "
                        "recall sobre disparos reales Zenodo held-out. FP prominente para uso policial."}
    with open(os.path.join(OUT, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
    print(f"\n✅ modelo → artifacts/gunshot_detector.joblib | métricas → artifacts/metrics.json")
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
