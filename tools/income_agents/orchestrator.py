#!/usr/bin/env python3
"""
orchestrator.py — AGENTE DE CONTENIDO automático end-to-end (SEAL income, dueña: ALICE 2026-06-24).

Encadena las piezas del equipo (cada hermano construyó una, yo orquesto):
  trends_source(ALICE) → trend_analyzer(FABLE, rankea por $-potencial)
    → video_producer.produce(JARVIS, guion+voz+MP4) → youtube_publisher(NEXUS, sube Short)

Correr con el python del sistema (las piezas stdlib se importan; el video corre en su venv
por subproceso porque necesita edge-tts):
  python3 orchestrator.py [geo] [--publish]
Sin --publish = dry-run (no sube; seguro hasta tener la cuenta de YouTube de William).

Honestidad (ALICE): motor automático y ~$0 por video; la PLATA del contenido rampa con la
audiencia. Esto produce el activo 24/7; el ingreso compone con el tiempo.
"""
import sys, os, json, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
VENV_PY = os.path.join(HERE, "content_factory", ".venv", "bin", "python")
VIDEO_PROD = os.path.join(HERE, "content_factory", "video_producer.py")


def pick_topic(geo="PE"):
    """trends reales → rankeados por potencial de monetización → el mejor tema."""
    import trends_source, trend_analyzer
    trends = trends_source.get_trends(geo=geo, limit=20)
    if not trends:
        return None, []
    ranked = trend_analyzer.rank_from_source(trends, top_n=10)
    top = _topic_of(ranked[0]) if ranked else trends[0].get("titulo")
    return top, ranked


def _topic_of(item):
    """rank_trends puede devolver dict o tupla — saco el string del tema robusto."""
    if isinstance(item, dict):
        return item.get("topic") or item.get("titulo") or item.get("title") or str(item)
    if isinstance(item, (list, tuple)) and item:
        return str(item[0])
    return str(item)


def make_video(nicho):
    """Corre video_producer en SU venv (edge-tts) y devuelve el dict {ok, mp4, ...}."""
    r = subprocess.run([VENV_PY, VIDEO_PROD, nicho], capture_output=True, text=True, timeout=420)
    out = r.stdout.strip()
    # el __main__ imprime JSON; tomo el último bloque JSON del stdout
    try:
        start = out.index("{")
        return json.loads(out[start:])
    except Exception:
        return {"ok": False, "reason": "no parse video_producer", "stdout": out[-300:], "stderr": r.stderr[-300:]}


def publish(video, dry_run=True):
    """Sube el MP4 como YouTube Short (dry-run hasta tener credenciales)."""
    import youtube_publisher as yp
    meta = yp.VideoMeta(
        title=(video.get("titulo") or "Short")[:95],
        description=(video.get("narracion") or "") + "\n\n",
        tags=video.get("hashtags") or [],
    )
    return yp.publish_short(video["mp4"], meta, dry_run=dry_run)


def run_once(geo="PE", do_publish=False):
    print(f"[1/3] eligiendo tema en tendencia ({geo})…")
    topic, ranked = pick_topic(geo)
    if not topic:
        return {"ok": False, "stage": "topic", "reason": "sin tendencias"}
    print(f"      tema: {topic!r}")
    print("[2/3] produciendo video (guion→voz→MP4)…")
    video = make_video(topic)
    if not video.get("ok"):
        return {"ok": False, "stage": "video", "detail": video}
    print(f"      MP4: {video['mp4']} ({video.get('dur_s')}s)")
    print(f"[3/3] publicando (dry_run={not do_publish})…")
    pub = publish(video, dry_run=not do_publish)
    return {"ok": True, "topic": topic, "video": video["mp4"], "publish": pub}


if __name__ == "__main__":
    geo = next((a for a in sys.argv[1:] if not a.startswith("-")), "PE")
    do_pub = "--publish" in sys.argv
    res = run_once(geo, do_publish=do_pub)
    print(json.dumps(res, ensure_ascii=False, indent=2))
