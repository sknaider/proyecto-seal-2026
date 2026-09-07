#!/usr/bin/env python3
"""
gunshot_localization_poc.py — POC de LOCALIZACIÓN de disparos (la pieza de FABLE).
================================================================================
El clasificador (JARVIS) responde "¿hubo disparo?". Esto responde "¿DÓNDE fue?" —
el diferenciador FORENSE-policial que NINGÚN repo open-source daba.

Método: GCC-PHAT (Generalized Cross-Correlation con Phase Transform) para estimar el
TDOA (time-difference-of-arrival) entre micrófonos — robusto a ruido y reverberación
(el PHAT blanquea el espectro). De los TDOA se obtiene:
  • DIRECCIÓN de llegada (DoA) con 2 micrófonos (campo lejano, onda plana).
  • POSICIÓN 2D (x,y) con 3+ micrófonos, por grid-search de multilateración TDOA.

Solo numpy (liviano, corre en el Pi/edge). Integra al POC junto al clasificador.
Autor: FABLE · 2026-06-24 · verificado por efecto con disparo sintético.
"""
import numpy as np

C_SOUND = 343.0  # m/s (aire ~20°C)


def gcc_phat(sig, refsig, fs, interp=16, max_tau=None):
    """TDOA de `sig` respecto a `refsig` vía GCC-PHAT. Devuelve (tau_seg, cc)."""
    n = sig.shape[0] + refsig.shape[0]
    SIG = np.fft.rfft(sig, n=n)
    REF = np.fft.rfft(refsig, n=n)
    R = SIG * np.conj(REF)
    R /= np.abs(R) + 1e-12                      # PHAT: blanquea (solo fase)
    cc = np.fft.irfft(R, n=interp * n)
    max_shift = int(interp * n / 2)
    if max_tau is not None:
        max_shift = min(int(interp * fs * max_tau), max_shift)
    cc = np.concatenate((cc[-max_shift:], cc[:max_shift + 1]))
    shift = np.argmax(np.abs(cc)) - max_shift
    return shift / float(interp * fs), cc


def doa_2mic(sig, refsig, fs, mic_dist):
    """Ángulo de llegada (grados) con 2 micrófonos. 0°=broadside, ±90°=endfire."""
    tau, _ = gcc_phat(sig, refsig, fs, max_tau=mic_dist / C_SOUND)
    arg = np.clip(C_SOUND * tau / mic_dist, -1.0, 1.0)
    return np.degrees(np.arcsin(arg)), tau


def localize_2d(signals, mic_pos, fs, grid_m=10.0, step=0.25):
    """
    Posición (x,y) de la fuente por multilateración TDOA (grid-search).
    signals: lista de señales por mic (mismo largo). mic_pos: [(x,y),...] en metros.
    Devuelve (x_est, y_est, error_rms_seg).
    """
    mic_pos = np.asarray(mic_pos, float)
    ref = signals[0]
    # TDOA medido de cada mic respecto al mic 0
    tdoa_meas = np.array([gcc_phat(signals[i], ref, fs)[0] for i in range(len(signals))])
    xs = np.arange(-grid_m, grid_m + step, step)
    ys = np.arange(-grid_m, grid_m + step, step)
    best, best_err = None, np.inf
    for x in xs:
        for y in ys:
            d = np.hypot(mic_pos[:, 0] - x, mic_pos[:, 1] - y)
            tdoa_pred = (d - d[0]) / C_SOUND
            err = np.sqrt(np.mean((tdoa_pred - tdoa_meas) ** 2))
            if err < best_err:
                best_err, best = err, (x, y)
    return best[0], best[1], best_err


# ───────────────────────── autotest POR EFECTO ─────────────────────────
def _gunshot(fs, n):
    """Impulso tipo muzzle-blast: pico abrupto + cola exponencial corta."""
    t = np.arange(n) / fs
    sig = np.exp(-t * 600) * np.sin(2 * np.pi * 800 * t)
    sig[0] += 5.0
    return sig


def _scene(base, fs, total, tau, rng, base_off=0.03, noise=0.02):
    """Inserta el disparo en un offset ENTERO (no circular) = base_off+tau seg, + ruido
    independiente. base_off mantiene los offsets positivos aunque tau sea negativo."""
    out = np.zeros(total)
    off = int(round((base_off + tau) * fs))
    out[off:off + len(base)] = base
    return out + noise * rng.standard_normal(total)


if __name__ == "__main__":
    import sys
    fs, n = 48000, 4096
    base = _gunshot(fs, n)
    total = 8192
    rng = np.random.default_rng(7)
    ok = True

    print("═══ TEST 1: DoA con 2 micrófonos (ángulo conocido) ═══")
    d = 0.30  # 30 cm de separación
    for true_deg in (0.0, 20.0, -35.0, 60.0):
        tau_true = d * np.sin(np.radians(true_deg)) / C_SOUND
        mic1 = _scene(base, fs, total, 0.0, rng)
        mic2 = _scene(base, fs, total, tau_true, rng)   # mic2 recibe después si ángulo>0
        est_deg, tau = doa_2mic(mic2, mic1, fs, d)
        err = abs(est_deg - true_deg)
        flag = "ok" if err < 5 else "FAIL"
        if err >= 5:
            ok = False
        print(f"  real {true_deg:+6.1f}° → estimado {est_deg:+6.1f}°  (err {err:.1f}°) [{flag}]")

    print("\n═══ TEST 2: posición 2D con 4 micrófonos (multilateración) ═══")
    mics = [(0, 0), (2, 0), (0, 2), (2, 2)]    # array cuadrado 2m
    for src in [(5.0, 7.0), (-4.0, 3.0)]:
        sigs = []
        d0 = np.hypot(mics[0][0] - src[0], mics[0][1] - src[1])
        for (mx, my) in mics:
            dist = np.hypot(mx - src[0], my - src[1])
            sigs.append(_scene(base, fs, total, (dist - d0) / C_SOUND, rng))
        x, y, e = localize_2d(sigs, mics, fs, grid_m=10, step=0.25)
        derr = np.hypot(x - src[0], y - src[1])
        flag = "ok" if derr < 0.6 else "FAIL"
        if derr >= 0.6:
            ok = False
        print(f"  real {src} → estimado ({x:.2f},{y:.2f})  (err {derr:.2f} m) [{flag}]")

    print(f"\n{'✅ LOCALIZACIÓN FUNCIONA' if ok else '⚠️ revisar'} — verificado por efecto")
    sys.exit(0 if ok else 1)
