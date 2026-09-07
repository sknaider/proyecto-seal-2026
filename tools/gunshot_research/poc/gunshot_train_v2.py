#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gunshot_train_v2.py — Detector CROSS-DATASET (fix del overfit-al-dataset de v1).
================================================================================
v1 daba 90% in-distribution pero 11.5% en disparos reales (Zenodo) → NO generalizaba.
v2: mete disparos REALES de Zenodo en el ENTRENO + los testea en un held-out de Zenodo
separado POR EVENTO (UUID), no por archivo → sin leakage (FABLE: los canales chanN+mean del
MISMO disparo no pueden cruzar train/test). Negativos = SESA (casual/explosion/siren).

Métrica que manda (uso policial): FALSO-POSITIVO + recall en disparos reales held-out.
"""
from __future__ import annotations
import os, glob, json, warnings, re
import numpy as np
warnings.filterwarnings("ignore")

from gunshot_train import (features_from_wave, load_wav, augment, sesa_split,
                           SESA, ZENODO, OUT, SR)

UUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def zenodo_by_event(n_events=300, seed=11):
    """Agrupa los wav de Zenodo por UUID de disparo. Devuelve dict {uuid: [paths]}.
    Muestra n_events disparos distintos (no archivos) para mantenerlo tratable."""
    wavs = sorted(glob.glob(os.path.join(ZENODO, "**", "*.wav"), recursive=True))
    groups = {}
    for p in wavs:
        m = UUID_RE.search(os.path.basename(p))
        key = m.group(1) if m else os.path.basename(p)
        groups.setdefault(key, []).append(p)
    keys = sorted(groups)
    rng = np.random.RandomState(seed)
    if len(keys) > n_events:
        keys = [keys[i] for i in rng.choice(len(keys), n_events, replace=False)]
    return {k: groups[k] for k in keys}


def feats_for_paths(paths, max_per_event=2):
    """Extrae features de hasta max_per_event canales por evento (evita sobre-pesar eventos
    con 7 canales). do_denoise=True (pipeline rcarioni)."""
    X = []
    for p in paths[:max_per_event]:
        try:
            X.append(features_from_wave(load_wav(p, do_denoise=True)))
        except Exception:
            continue
    return X


def main():
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.svm import SVC
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    from sklearn.metrics import precision_score, recall_score, f1_score, confusion_matrix

    print("[1/5] SESA: negativos (todos) + positivos gunshot, denoise+augment…")
    Xs_tr, ys_tr = sesa_split("train", do_denoise=True, augment_pos=2)
    Xs_te, ys_te = sesa_split("test",  do_denoise=True, augment_pos=0)

    print("[2/5] Zenodo agrupado por EVENTO (UUID) y split 70/30 SIN leakage…")
    groups = zenodo_by_event(n_events=300)
    keys = list(groups)
    rng = np.random.RandomState(3)
    rng.shuffle(keys)
    cut = int(0.7 * len(keys))
    train_keys, test_keys = keys[:cut], keys[cut:]
    print(f"      eventos Zenodo: {len(keys)}  train={len(train_keys)} test={len(test_keys)}")

    Xz_tr = [f for k in train_keys for f in feats_for_paths(groups[k])]
    Xz_te = [f for k in test_keys  for f in feats_for_paths(groups[k])]
    print(f"      muestras Zenodo: train={len(Xz_tr)} test={len(Xz_te)}")

    # Conjunto de entrenamiento = SESA(train) + Zenodo(train) positivos
    X_train = np.vstack([Xs_tr, np.array(Xz_tr)]) if Xz_tr else Xs_tr
    y_train = np.concatenate([ys_tr, np.ones(len(Xz_tr), dtype=int)])
    print(f"[3/5] Entreno total: {X_train.shape}  positivos={int(y_train.sum())}/{len(y_train)}")

    models = {
        "random_forest": make_pipeline(StandardScaler(),
            RandomForestClassifier(n_estimators=500, class_weight="balanced", random_state=0)),
        "svm_rbf": make_pipeline(StandardScaler(),
            SVC(C=10, gamma="scale", class_weight="balanced", probability=True, random_state=0)),
    }

    print("[4/5] Eval: (a) SESA test out-of-sample  (b) Zenodo HELD-OUT real (cross-event)…")
    report = {}
    best = (None, None, -1)
    for name, m in models.items():
        m.fit(X_train, y_train)
        # (a) SESA test
        pa = m.predict(Xs_te)
        tn, fp, fn, tp = confusion_matrix(ys_te, pa, labels=[0, 1]).ravel()
        sesa = {"precision": round(precision_score(ys_te, pa, zero_division=0), 4),
                "recall": round(recall_score(ys_te, pa, zero_division=0), 4),
                "f1": round(f1_score(ys_te, pa, zero_division=0), 4),
                "false_positives": int(fp), "false_negatives": int(fn)}
        # (b) Zenodo held-out (solo positivos → recall en disparos reales nunca vistos)
        zrec = None
        if Xz_te:
            zp = m.predict(np.array(Xz_te))
            zrec = round(float((zp == 1).mean()), 4)
        report[name] = {"sesa_test": sesa, "zenodo_heldout_recall": zrec,
                        "zenodo_heldout_n": len(Xz_te)}
        print(f"   · {name:14s} SESA: prec={sesa['precision']} rec={sesa['recall']} FP={sesa['false_positives']} "
              f"| Zenodo-real recall={zrec}")
        score = (zrec or 0) - 0.5 * sesa["false_positives"]   # premia generalización, penaliza FP
        if score > best[2]:
            best = (name, m, score)

    print(f"[5/5] Mejor por generalización: {best[0]}")
    import joblib
    joblib.dump(best[1], os.path.join(OUT, "gunshot_detector_v2.joblib"))
    metrics = {"version": "v2_cross_dataset", "best_model": best[0], "models": report,
               "improvement_note": "v1 Zenodo-real recall=0.115 (solo SESA en train). v2 mete "
                                    "Zenodo real en train con split por EVENTO (UUID) anti-leakage."}
    with open(os.path.join(OUT, "metrics_v2.json"), "w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
    print("\n" + json.dumps(metrics, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
