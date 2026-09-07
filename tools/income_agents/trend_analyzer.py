#!/usr/bin/env python3
"""
trend_analyzer.py — selección de tendencias por POTENCIAL DE MONETIZACIÓN (pieza de FABLE).
==========================================================================================
Alimenta el content_agent (ALICE) con el TEMA antes del guion. El rigor central: un tema
viral genérico (baile/comedia) tiene RPM bajísimo; un tema de finanzas/tech/negocios con
momentum tiene RPM 5-10× más. Este módulo NO elige el más viral — elige el de mayor
$-potencial = momentum × RPM-del-nicho. Así el content_agent scripta lo que PAGA.

Pieza del stack: TREND(FABLE) → GUION(ALICE) → VOZ → VIDEO → POST. Todo local/gratis.
Núcleo puro (ranking) testeable sin red; fetcher de Google Trends opcional (pytrends).
Autor: FABLE · 2026-06-24 · verificado por efecto.
"""
from __future__ import annotations

# RPM aproximado (USD por 1000 views monetizadas) por nicho — rangos públicos conocidos de YouTube.
# El número es el punto medio realista; lo que importa es el ORDEN relativo.
NICHE_RPM = {
    "finanzas":      22.0,   # inversión, cripto, bolsa, dinero
    "negocios":      18.0,   # marketing, emprender, ventas
    "tech":          15.0,   # software, IA, gadgets, programación
    "educacion":     11.0,   # how-to, tutoriales, cursos
    "salud":          9.0,   # fitness, nutrición, bienestar
    "noticias":       7.0,   # actualidad, política
    "gaming":         5.0,
    "entretenimiento": 2.5,  # comedia, vlogs, reacciones
    "musica":         1.5,   # baile, música
    "general":        4.0,   # sin clasificar
}

# Palabras-clave → nicho (para clasificar un tema trending).
NICHE_KEYWORDS = {
    "finanzas": ["bolsa", "cripto", "bitcoin", "invertir", "inversion", "dinero", "dolar",
                 "acciones", "forex", "trading", "finanzas", "ahorro", "deuda", "interes"],
    "negocios": ["negocio", "emprender", "marketing", "ventas", "startup", "freelance",
                 "ecommerce", "pyme", "cliente", "vender"],
    "tech": ["ia", "inteligencia artificial", "chatgpt", "software", "programar", "python",
             "app", "iphone", "android", "gadget", "tecnologia", "robot"],
    "educacion": ["como", "tutorial", "aprender", "curso", "guia", "explicado", "tips", "pasos"],
    "salud": ["fitness", "gym", "dieta", "nutricion", "salud", "ejercicio", "adelgazar", "peso"],
    "noticias": ["noticia", "ultima hora", "politica", "eleccion", "gobierno", "crisis"],
    "gaming": ["gameplay", "juego", "game", "minecraft", "fortnite", "roblox", "gamer"],
    "entretenimiento": ["comedia", "reaccion", "vlog", "challenge", "famoso", "viral", "meme"],
    "musica": ["cancion", "musica", "baile", "dance", "remix", "letra"],
}


import re


def classify_topic(topic: str) -> str:
    """Clasifica un tema en un nicho por palabras-clave (límite de PALABRA, no substring —
    así 'ia' no matchea 'noticia'/'trafico'). Default 'general'."""
    t = topic.lower()
    for niche, kws in NICHE_KEYWORDS.items():
        if any(re.search(r"\b" + re.escape(kw) + r"\b", t) for kw in kws):
            return niche
    return "general"


def _parse_traffic(s) -> float:
    """'50000+' / '1000+' → float (momentum proxy). Vacío → 50."""
    if not s:
        return 50.0
    digits = re.sub(r"[^0-9]", "", str(s))
    return float(digits) if digits else 50.0


def rank_from_source(items, top_n: int = 10):
    """Adapta la salida de trends_source.get_trends() de ALICE
    ([{'titulo','trafico','noticia'}]) → (topic, momentum) y rankea por $-potencial.
    El momentum se NORMALIZA (log del tráfico) para que un tema de 50000+ no aplaste
    todo: lo que decide es nicho(RPM) × momentum, no el tráfico bruto."""
    import math
    raw = []
    for it in items or []:
        if isinstance(it, dict):
            titulo = it.get("titulo") or it.get("title") or it.get("topic") or ""
            traf = _parse_traffic(it.get("trafico") or it.get("traffic"))
        elif isinstance(it, (list, tuple)) and len(it) >= 2:
            titulo, traf = str(it[0]), _parse_traffic(it[1])
        else:
            titulo, traf = str(it), 50.0
        if titulo:
            # momentum 0..100 vía log (comprime el rango de tráfico)
            mom = min(100.0, 10.0 * math.log10(max(traf, 10)))
            raw.append((titulo, mom))
    return rank_trends(raw, top_n=top_n)


def rank_trends(trends, top_n: int = 10):
    """
    trends: iterable de (topic:str, momentum:float)  — momentum = interés/rising 0..100.
    Devuelve lista ordenada por $-POTENCIAL = momentum × RPM-del-nicho (no solo momentum).
    Cada item: {topic, niche, momentum, rpm, score, est_rpm_signal}.
    """
    out = []
    for topic, momentum in trends:
        niche = classify_topic(topic)
        rpm = NICHE_RPM.get(niche, NICHE_RPM["general"])
        score = float(momentum) * rpm        # el corazón: pondera momentum por monetización
        out.append({
            "topic": topic, "niche": niche, "momentum": round(float(momentum), 1),
            "rpm": rpm, "score": round(score, 1),
        })
    out.sort(key=lambda d: d["score"], reverse=True)
    return out[:top_n]


def fetch_google_trends(geo="PE", count=20):
    """Tendencias reales de Google Trends (pytrends). Devuelve [(topic, momentum)] o None
    si pytrends no está instalado (el módulo sigue usable con datos provistos a rank_trends)."""
    try:
        from pytrends.request import TrendReq
    except Exception:
        return None
    try:
        py = TrendReq(hl="es", tz=300)
        df = py.trending_searches(pn="peru" if geo == "PE" else "united_states")
        topics = df[0].tolist()[:count]
        # pytrends trending no da score directo; usamos rango decreciente como momentum proxy
        n = len(topics)
        return [(t, 100.0 * (n - i) / n) for i, t in enumerate(topics)]
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    print("═══ TEST: ranking por $-potencial (no por viralidad cruda) ═══")
    # mezcla: entretenimiento MUY viral vs finanzas/tech momentum medio
    synthetic = [
        ("Baile viral de TikTok del momento", 95),       # musica/entret, RPM bajo
        ("Cómo invertir en la bolsa desde cero", 60),     # finanzas, RPM alto
        ("Nuevo meme viral", 90),                         # entretenimiento, RPM bajo
        ("ChatGPT para automatizar tu negocio", 55),      # tech/negocios, RPM alto
        ("Reaccion a video famoso", 88),                  # entretenimiento
        ("Tips para ahorrar dinero en 2026", 50),         # finanzas
    ]
    ranked = rank_trends(synthetic)
    print(f"{'#':<3}{'tema':<42}{'nicho':<14}{'mom':>5}{'rpm':>6}{'score':>8}")
    for i, r in enumerate(ranked, 1):
        print(f"{i:<3}{r['topic'][:40]:<42}{r['niche']:<14}{r['momentum']:>5}{r['rpm']:>6}{r['score']:>8}")
    # verificación por efecto: el #1 debe ser de nicho monetizable (finanzas/tech/negocios),
    # NO el baile viral (95 momentum pero RPM 1.5), pese a tener menos momentum.
    top = ranked[0]
    ok = top["niche"] in ("finanzas", "tech", "negocios", "educacion")
    print(f"\nTop = '{top['topic'][:40]}' (nicho {top['niche']})")
    print(f"{'✅ prioriza MONETIZACIÓN sobre viralidad cruda' if ok else '⚠️ revisar'} — verificado por efecto")
    # confirmar que el baile viral (mom 95) NO quedó #1
    baile = next(r for r in ranked if "Baile" in r["topic"])
    ok2 = ranked.index(baile) > 0
    print(f"{'✅ el viral-vacío (mom 95) NO es #1' if ok2 else '⚠️ el viral ganó'} — RPM lo bajó")
    sys.exit(0 if (ok and ok2) else 1)
