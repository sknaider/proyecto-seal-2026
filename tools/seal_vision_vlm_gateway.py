"""SOUL Vision — GATEWAY seguro del VLM (carril NEXUS, Spark-side).

Envuelve las funciones VLM de JARVIS (soul_vision/) con la CAPA DE SEGURIDAD de NEXUS:
recibe un FRAME (la imagen viaja 5070→Spark — opt-in de William «todos los datos al modelo»),
lo entiende (Qwen2.5-VL Apache, local en el Spark) y opcionalmente lo recuerda con custodia.

Consumidores (contrato JARVIS): la app de ALICE (/api/describe → acá) y el push del 5070.

SEGURIDAD (lo que JARVIS marcó + mi hallazgo RLS):
  · Auth: header X-SEAL-Vision-Token (de ~/.config/seal/vision_ingest_token, chmod 600).
  · TLS: el frame NO viaja en claro — correr uvicorn con --ssl-keyfile/--ssl-certfile (tailnet).
  · Rol RESTRINGIDO: el path 'remember' conecta con SOUL_VISION_DSN (rol NO-superuser cuando exista;
    hoy funciona con seal pero seal BYPASSA RLS → hardening #938). El frame NO va por superuser en claro.
  · MINIMIZACIÓN: el frame se guarda en temp con 0600, se ENTIENDE, y se BORRA siempre (finally).
    No persiste raw salvo política (la evidencia es el frame_hash + descripción, no la imagen).

Contrato HTTP:
  POST /api/vision/ingest  (multipart)
    file=<frame>  · mode=describe|remember  · prompt=<opt>  · camera_id=<opt, req si remember>
    header X-SEAL-Vision-Token: <token>
  describe → {ok, description, model_id, license}
  remember → {ok, description, event_id, chain_hash, memory_id}
  GET /api/vision/health → {ok, vlm}

Correr (Spark): uvicorn seal_vision_vlm_gateway:app --host <tailnet> --port 8771 \
    --ssl-keyfile key.pem --ssl-certfile cert.pem
"""
from __future__ import annotations
import os, sys, hmac, tempfile

# soul_vision en el path (vlm_describe / vlm_scene_memory de JARVIS)
_SV = os.environ.get("SOUL_VISION_DIR", "/home/dadito/IA/proyecto-seal/memory/soul_vision")
if _SV not in sys.path:
    sys.path.insert(0, _SV)

from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException

TOKEN_PATH = os.path.expanduser("~/.config/seal/vision_ingest_token")
DSN = os.environ.get("SOUL_VISION_DSN", "")  # rol restringido cuando exista (no hardcode)
_MAX_FRAME = 12 * 1024 * 1024  # 12MB cap anti-abuso

app = FastAPI(title="SOUL Vision VLM Gateway (NEXUS)")


def _load_token() -> str:
    try:
        with open(TOKEN_PATH, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def _check(token: str | None) -> bool:
    real = _load_token()
    return bool(real) and bool(token) and hmac.compare_digest(real, token)


@app.get("/api/vision/health")
def health():
    try:
        import vlm_describe
        return {"ok": True, "vlm": vlm_describe.available()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.post("/api/vision/ingest")
async def ingest(file: UploadFile = File(...), mode: str = Form("describe"),
                 prompt: str | None = Form(None), camera_id: str | None = Form(None),
                 x_seal_vision_token: str | None = Header(None)):
    if not _check(x_seal_vision_token):
        raise HTTPException(status_code=401, detail="unauthorized")
    if mode not in ("describe", "remember"):
        raise HTTPException(status_code=400, detail="mode debe ser describe|remember")
    if mode == "remember" and not camera_id:
        raise HTTPException(status_code=400, detail="camera_id requerido para remember")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="frame vacío")
    if len(data) > _MAX_FRAME:
        raise HTTPException(status_code=413, detail="frame demasiado grande")

    # MINIMIZACIÓN: temp 0600, entender, BORRAR siempre
    fd, path = tempfile.mkstemp(suffix=".jpg", prefix="sv_frame_")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        import vlm_describe
        if mode == "describe":
            res = vlm_describe.describe(path, prompt or vlm_describe.DEFAULT_PROMPT)
            return {"ok": True, "description": res.get("description"),
                    "model_id": res.get("model_id"), "license": res.get("license")}
        # remember: conn (rol restringido cuando exista) + custodia + memoria
        if not DSN:
            raise HTTPException(status_code=503, detail="SOUL_VISION_DSN no configurado para remember")
        import asyncpg, vlm_scene_memory
        conn = await asyncpg.connect(DSN, timeout=15)
        try:
            r = await vlm_scene_memory.understand_and_remember(conn, path, camera_id)
        finally:
            await conn.close()
        return {"ok": True, "description": r.get("description"), "event_id": r.get("event_id"),
                "chain_hash": r.get("chain_hash"), "memory_id": r.get("memory_id")}
    finally:
        try:
            os.remove(path)   # frame NUNCA persiste raw
        except OSError:
            pass
