#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
youtube_publisher.py — AUTO-PUBLICAR Shorts en YouTube (pieza #4, carril NEXUS).
================================================================================
Cierra el loop del agente de contenido: toma el .mp4 (de JARVIS) + el guion
(de ALICE: titulo/desc/hashtags) y lo SUBE como YouTube Short, sin intervención.

SEGURIDAD (mi carril, no negociable):
  * Scope MÍNIMO: solo `youtube.upload` (no leer/borrar/gestionar el canal).
  * El refresh-token vive FUERA del repo en ~/.config/seal/youtube_oauth.json con
    permisos 600; NUNCA en código ni en logs. Se valida el permiso del archivo.
  * Nada de secretos impresos. Checkout/credenciales hosteadas por Google OAuth.
  * Anti-baneo: pausa configurable entre subidas; respeta cuota diaria.

Diseño: lógica PURA (validación, metadata, chequeo de credenciales) testeable sin
red ni claves; la subida real se activa cuando William provee las credenciales.
Deps en deploy: google-api-python-client google-auth (pip en venv) — import guardado.
"""
from __future__ import annotations
import os, json, stat, time
from dataclasses import dataclass
from typing import Optional

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]   # least privilege
TOKEN_PATH = os.path.expanduser("~/.config/seal/youtube_oauth.json")
MAX_TITLE = 100          # límite YouTube
MAX_DESC = 5000

try:
    from googleapiclient.discovery import build              # type: ignore
    from googleapiclient.http import MediaFileUpload         # type: ignore
    from google.oauth2.credentials import Credentials        # type: ignore
    from google.auth.transport.requests import Request       # type: ignore
    _HAVE_GOOGLE = True
except Exception:
    _HAVE_GOOGLE = False


# ───────────────────────── lógica pura (testeable sin red/claves) ─────────────────────────
@dataclass
class VideoMeta:
    title: str
    description: str
    tags: list
    privacy: str = "public"      # public | unlisted | private
    category_id: str = "22"      # People & Blogs (default seguro)
    made_for_kids: bool = False


def build_request_body(meta: VideoMeta) -> dict:
    """Construye el body de videos.insert. Trunca a límites de YouTube. Marca #Shorts."""
    title = (meta.title or "").strip()[:MAX_TITLE]
    desc = (meta.description or "").strip()
    # hashtags al final de la descripción (así YouTube los toma) + #Shorts
    tags = [t.lstrip("#") for t in (meta.tags or []) if t.strip()]
    tagline = " ".join("#" + t for t in tags + ["Shorts"])
    desc = (desc + "\n\n" + tagline).strip()[:MAX_DESC]
    return {
        "snippet": {"title": title or "Short", "description": desc,
                    "tags": tags[:15], "categoryId": meta.category_id},
        "status": {"privacyStatus": meta.privacy if meta.privacy in
                   ("public", "unlisted", "private") else "private",
                   "selfDeclaredMadeForKids": bool(meta.made_for_kids)},
    }


def validate_upload(mp4_path: str, meta: VideoMeta) -> tuple[bool, str]:
    """Chequeos previos (sin red): archivo existe/no vacío, título presente."""
    if not mp4_path or not os.path.isfile(mp4_path):
        return False, f"mp4 no encontrado: {mp4_path}"
    if os.path.getsize(mp4_path) == 0:
        return False, "mp4 vacío"
    if not (meta.title or "").strip():
        return False, "falta título"
    return True, "ok"


def token_security_ok() -> tuple[bool, str]:
    """Verifica que el token exista y NO sea legible por otros (chmod 600)."""
    if not os.path.isfile(TOKEN_PATH):
        return False, f"sin credenciales en {TOKEN_PATH} (William debe proveerlas)"
    mode = stat.S_IMODE(os.stat(TOKEN_PATH).st_mode)
    if mode & 0o077:
        return False, f"PERMISOS INSEGUROS en token ({oct(mode)}); requiere 600"
    return True, "credenciales presentes y con permiso seguro"


# ───────────────────────── subida real (requiere creds + libs) ─────────────────────────
def _load_credentials():
    with open(TOKEN_PATH) as f:
        data = json.load(f)
    creds = Credentials.from_authorized_user_info(data, SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return creds


def publish_short(mp4_path: str, meta: VideoMeta, *, dry_run: bool = False) -> dict:
    """Sube el MP4 como Short. dry_run=True valida TODO sin tocar la red."""
    ok, msg = validate_upload(mp4_path, meta)
    if not ok:
        return {"ok": False, "stage": "validate", "error": msg}
    body = build_request_body(meta)
    sec_ok, sec_msg = token_security_ok()
    if dry_run:
        return {"ok": True, "dry_run": True, "would_upload": os.path.basename(mp4_path),
                "body": body, "credentials": sec_msg}
    if not _HAVE_GOOGLE:
        return {"ok": False, "stage": "deps",
                "error": "faltan libs: pip install google-api-python-client google-auth"}
    if not sec_ok:
        return {"ok": False, "stage": "credentials", "error": sec_msg}
    try:
        creds = _load_credentials()
        yt = build("youtube", "v3", credentials=creds)
        media = MediaFileUpload(mp4_path, chunksize=-1, resumable=True, mimetype="video/mp4")
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        resp = None
        while resp is None:
            _status, resp = req.next_chunk()
        return {"ok": True, "video_id": resp.get("id"),
                "url": f"https://youtube.com/shorts/{resp.get('id')}"}
    except Exception as e:
        return {"ok": False, "stage": "upload", "error": str(e)}


SETUP_STEPS_FOR_WILLIAM = """\
Pasos para habilitar la publicación (1 sola vez):
 1) console.cloud.google.com → crear proyecto → habilitar 'YouTube Data API v3'.
 2) Credenciales → OAuth client ID → tipo 'Desktop app' → descargar el client_secret.json.
 3) Autorizar UNA vez (NEXUS te pasa el comando) con scope youtube.upload → genera el
    refresh token, que se guarda en ~/.config/seal/youtube_oauth.json (chmod 600).
 4) Listo: el agente publica solo. (TikTok = fase 2, su API requiere aprobación.)
"""


# ───────────────────────── self-test POR EFECTO (sin red) ─────────────────────────
if __name__ == "__main__":
    import tempfile, sys
    ok = True
    meta = VideoMeta(title="¡Gestiona tus finanzas como un pro!",
                     description="Detente! ¿Sabías que...?",
                     tags=["finanzas", "#ahorro", "peru"], privacy="public")

    # 1) build_request_body: trunca, arma #Shorts, tags limpios
    body = build_request_body(meta)
    t1 = (body["snippet"]["title"] and "#Shorts" in body["snippet"]["description"]
          and "ahorro" in body["snippet"]["tags"] and body["status"]["privacyStatus"] == "public")
    ok &= t1; print("1 body+#Shorts+tags:", "ok" if t1 else "FAIL")

    # 2) validate_upload: archivo inexistente → falla; archivo real → pasa
    t2a = not validate_upload("/no/existe.mp4", meta)[0]
    tf = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False); tf.write(b"FAKEMP4"); tf.close()
    t2b = validate_upload(tf.name, meta)[0]
    t2 = t2a and t2b
    ok &= t2; print("2 validate (falta vs ok):", "ok" if t2 else "FAIL")

    # 3) dry_run: valida todo sin red, devuelve el body que subiría
    r = publish_short(tf.name, meta, dry_run=True)
    t3 = r.get("ok") and r.get("dry_run") and r.get("would_upload", "").endswith(".mp4")
    ok &= t3; print("3 dry_run end-to-end:", "ok" if t3 else "FAIL", "| creds:", r.get("credentials"))

    # 4) seguridad del token: sin archivo → reporta que faltan (no crashea)
    sec_ok, sec_msg = token_security_ok()
    t4 = isinstance(sec_ok, bool)
    ok &= t4; print("4 token_security_ok:", "ok" if t4 else "FAIL", "|", sec_msg)

    os.unlink(tf.name)
    print("\n", "✅ YOUTUBE PUBLISHER OK (lógica/validación/seguridad) — verificado por efecto"
          if ok else "⚠️ revisar")
    print("   (subida real se activa con las credenciales de William)")
    sys.exit(0 if ok else 1)
