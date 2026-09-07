#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gunshot_classifier_poc.py — POC del clasificador de audio (pieza JARVIS).
=========================================================================
Integra la METODOLOGÍA zero-falsos-positivos de rcarioni (extraída, NO forkeada — repo sin
licencia, ver veredicto) sobre datos LEGALES, para el software de investigación policial LOCAL.

Pipeline (clean-room):
  audio .wav → denoise (spectral gating, rcarioni) → augmentation (opcional, train) →
  features MFCC + deltas → CNN → {disparo, no-disparo}

Datos: SESA (anti-falso-positivo, ya bajado) + escalable a CADRE/NIJ + Zenodo (localización).
Deps para ENTRENAR: librosa, noisereduce, numpy, tensorflow/torch (instalar en venv del cluster).
El módulo está estructurado para correr en el cluster SEAL; aquí es el scaffold runnable + verificado.

Autor: JARVIS (Team SEAL) — director del proyecto. 2026-06-24.
"""
from __future__ import annotations
import os
import glob

SR = 8000                 # rcarioni usa 8 kHz (suficiente para la firma del disparo)
N_MFCC = 40               # MFCC base
USE_DELTAS = True         # MFCC + delta + delta-delta (rcarioni: mejora la precisión)
DATA_ROOT = os.path.join(os.path.dirname(__file__), "..", "datasets", "audio")


# ───────────────────────── DENOISE (metodología rcarioni) ─────────────────────────
def denoise_spectral(wave, sr: int = SR):
    """Spectral gating (rcarioni): abre la 'compuerta' solo sobre el umbral de ruido por banda.
    Reduce falsos positivos al limpiar el ambiente antes de clasificar."""
    import noisereduce as nr
    return nr.reduce_noise(y=wave, sr=sr, stationary=True, win_length=256, hop_length=128)


# ───────────────────────── AUGMENTATION (metodología rcarioni, solo train) ─────────
def augment(wave, sr: int = SR):
    """Gaussian noise + time-reverse + pitch-shift ±4 semitonos + time-stretch.
    Multiplica el dataset escaso de disparos (clave para 'potente')."""
    import numpy as np, librosa
    out = wave.copy()
    if np.random.rand() < 0.5:
        out = out + 0.005 * np.random.randn(len(out)).astype(out.dtype)   # gaussian
    if np.random.rand() < 0.3:
        out = out[::-1]                                                    # time-reverse
    if np.random.rand() < 0.4:
        out = librosa.effects.pitch_shift(out, sr=sr, n_steps=np.random.uniform(-4, 4))
    if np.random.rand() < 0.4:
        out = librosa.effects.time_stretch(out, rate=np.random.uniform(0.9, 1.1))
    return out


# ───────────────────────── FEATURES (MFCC + deltas, rcarioni) ──────────────────────
def extract_features(wave, sr: int = SR):
    import numpy as np, librosa
    mfcc = librosa.feature.mfcc(y=wave, sr=sr, n_mfcc=N_MFCC)
    feats = [mfcc]
    if USE_DELTAS:
        feats.append(librosa.feature.delta(mfcc))
        feats.append(librosa.feature.delta(mfcc, order=2))
    return np.stack(feats, axis=-1)   # (n_mfcc, t, canales)


# ───────────────────────── DATA LOADING ───────────────────────────────────────────
def load_clip(path: str, sr: int = SR, do_denoise: bool = True):
    import librosa
    wave, _ = librosa.load(path, sr=sr)
    if do_denoise:
        wave = denoise_spectral(wave, sr)
    return wave


def discover_wavs(root: str = DATA_ROOT):
    """Encuentra todos los .wav bajo datasets/audio/ (SESA + futuros)."""
    return glob.glob(os.path.join(root, "**", "*.wav"), recursive=True)


# ───────────────────────── MODELO (CNN, estilo rcarioni) ───────────────────────────
def build_model(input_shape, n_classes: int = 2):
    """CNN sobre espectrograma MFCC. Se entrena en el cluster SEAL (TF o torch)."""
    from tensorflow import keras
    from tensorflow.keras import layers
    m = keras.Sequential([
        layers.Input(shape=input_shape),
        layers.Conv2D(32, 3, activation="relu", padding="same"), layers.MaxPool2D(),
        layers.Conv2D(64, 3, activation="relu", padding="same"), layers.MaxPool2D(),
        layers.Conv2D(128, 3, activation="relu", padding="same"), layers.GlobalAveragePooling2D(),
        layers.Dropout(0.4),
        layers.Dense(64, activation="relu"),
        layers.Dense(n_classes, activation="softmax"),
    ])
    m.compile(optimizer="adam", loss="sparse_categorical_crossentropy",
              metrics=["accuracy", keras.metrics.Precision(name="precision")])
    return m


def selftest():
    """Verificación estructural sin deps pesadas: descubre datos + valida rutas."""
    wavs = discover_wavs()
    print(f"[POC] .wav encontrados bajo datasets/audio/: {len(wavs)}")
    print(f"[POC] SR={SR} N_MFCC={N_MFCC} deltas={USE_DELTAS}")
    print("[POC] pipeline: load→denoise(spectral rcarioni)→augment→MFCC+deltas→CNN")
    print("[POC] scaffold OK — entrenar en cluster con: librosa, noisereduce, tensorflow")
    return len(wavs)


if __name__ == "__main__":
    selftest()
