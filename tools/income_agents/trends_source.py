#!/usr/bin/env python3
"""
trends_source.py — Fuente de TENDENCIAS para el agente de contenido (SEAL income, lead ALICE).
William (2026-06-24): "buscar tendencias analíticas... en Google, para YouTube y TikTok".

Trae lo que está EN TENDENCIA AHORA (gratis, sin API key, sin cuenta) vía el RSS de
Google Trends Daily, y lo entrega como lista de temas para que content_agent.py genere
guiones sobre lo que la gente YA está buscando = más alcance = monetización más rápida.

geo: PE (Perú) por defecto; admite US, MX, etc. Correr:  python3 trends_source.py [geo]
Integra con content_agent:  for tema in get_trends('PE'): generar_guion(tema)

Nota (ALICE): Google Trends refleja búsquedas reales = buen proxy de demanda. YouTube/TikTok
trending necesitan API/scraping (pieza siguiente); este RSS es el quick-win data-real.
"""
import sys, re, urllib.request
from xml.etree import ElementTree as ET

# Endpoint VIGENTE (2026): Google deprecó /trendingsearches/daily/rss (404) → ahora /trending/rss.
RSS = "https://trends.google.com/trending/rss?geo={geo}"


def get_trends(geo: str = "PE", limit: int = 20):
    """Devuelve [{'titulo', 'trafico', 'noticia'}] de Google Trends Daily para el país."""
    url = RSS.format(geo=geo)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        xml = r.read()
    root = ET.fromstring(xml)
    ns = {"ht": "https://trends.google.com/trending/rss"}
    out = []
    for item in root.iter("item"):
        titulo = (item.findtext("title") or "").strip()
        if not titulo:
            continue
        trafico = item.findtext("ht:approx_traffic", default="", namespaces=ns) or ""
        noticia = item.findtext("ht:news_item_title", default="", namespaces=ns) or ""
        out.append({"titulo": titulo, "trafico": trafico.strip(), "noticia": noticia.strip()})
        if len(out) >= limit:
            break
    return out


if __name__ == "__main__":
    geo = sys.argv[1] if len(sys.argv) > 1 else "PE"
    trends = get_trends(geo)
    print(f"== Tendencias Google ({geo}) — {len(trends)} ==")
    for i, t in enumerate(trends, 1):
        extra = f"  [{t['trafico']}]" if t["trafico"] else ""
        print(f"{i:2}. {t['titulo']}{extra}")
