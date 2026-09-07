#!/usr/bin/env python3
"""GTL Gmail Inbox Fetcher — lee correos con PDFs AWB/DAM y los descarga para procesamiento.

Filtra: subject "PRE ALERT // AWB:" con PDF adjunto.
Marca procesados con label "GTL/Procesado" para no reprocesar.

Uso:
    python3 gmail_inbox_fetcher.py --once        # corre 1 vez y sale
    python3 gmail_inbox_fetcher.py --once --dry-run  # sin descargar ni marcar
    python3 gmail_inbox_fetcher.py --watch       # loop cada 5 min
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ── Config ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
CREDS_FILE = SCRIPT_DIR / "gmail_credentials.json"
TOKEN_FILE = SCRIPT_DIR / "gmail_token.json"
# Persistencia (Henry 2026-06-03): antes /tmp/gtl_inbox → se BORRABA al reiniciar el DGX
# o con la limpieza de /tmp, perdiendo los PDFs de los correos. Ahora dir PERMANENTE junto
# al script (awb_automation/inbox_pdfs), configurable por env GTL_INBOX_DIR. Fix NEXUS.
PDF_DOWNLOAD_DIR = Path(os.environ.get("GTL_INBOX_DIR", "/home/dadito/gtl_pipeline/inbox_pdfs"))
PDF_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]
QUERY = 'subject:"PRE ALERT // AWB:" has:attachment filename:pdf'
PROCESSED_LABEL = "GTL/Procesado"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("gmail_fetcher")


# ── Auth ────────────────────────────────────────────────────────────────────
def get_credentials() -> Credentials:
    """Obtiene credenciales OAuth. Primera vez abre browser; después usa token cache."""
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("Refrescando token OAuth...")
            creds.refresh(Request())
        else:
            if not CREDS_FILE.exists():
                raise FileNotFoundError(f"Falta {CREDS_FILE} — descargar de Google Cloud Console")
            log.info("Primer auth: abriendo browser para autorizar...")
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
        os.chmod(TOKEN_FILE, 0o600)
        log.info(f"Token guardado en {TOKEN_FILE}")
    return creds


# ── Label helpers ───────────────────────────────────────────────────────────
def get_or_create_label(service, label_name: str) -> str:
    """Devuelve el ID del label, creándolo si no existe (incluye sub-labels via /)."""
    results = service.users().labels().list(userId="me").execute()
    for label in results.get("labels", []):
        if label["name"] == label_name:
            return label["id"]
    log.info(f"Creando label: {label_name}")
    new_label = service.users().labels().create(
        userId="me",
        body={
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        },
    ).execute()
    return new_label["id"]


# ── Inbox fetch ─────────────────────────────────────────────────────────────
def list_unprocessed_messages(service, processed_label_id: str, limit: int = 50) -> list[str]:
    """Lista IDs de mensajes que matchean el filtro Y NO tienen label Procesado."""
    full_query = f"{QUERY} -label:{PROCESSED_LABEL.replace('/', '-')}"
    log.info(f"Query Gmail: {full_query}")
    result = service.users().messages().list(
        userId="me", q=full_query, maxResults=limit,
    ).execute()
    return [m["id"] for m in result.get("messages", [])]


def download_pdfs(service, msg_id: str) -> dict:
    """Descarga todos los PDFs adjuntos del mensaje. Devuelve dict con metadata + paths."""
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()

    headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
    subject = headers.get("Subject", "(no subject)")
    sender = headers.get("From", "(unknown)")
    date_str = headers.get("Date", "")

    pdf_paths = []
    parts = []

    def _walk(part):
        if "parts" in part:
            for sub in part["parts"]:
                _walk(sub)
        else:
            parts.append(part)

    _walk(msg["payload"])

    for part in parts:
        filename = part.get("filename", "")
        if not filename.lower().endswith(".pdf"):
            continue
        body = part.get("body", {})
        att_id = body.get("attachmentId")
        if not att_id:
            continue
        att = service.users().messages().attachments().get(
            userId="me", messageId=msg_id, id=att_id,
        ).execute()
        data = base64.urlsafe_b64decode(att["data"])
        # Nombre único: msgId_filename
        safe_name = filename.replace("/", "_").replace(" ", "_")
        dest = PDF_DOWNLOAD_DIR / f"{msg_id}_{safe_name}"
        dest.write_bytes(data)
        pdf_paths.append(str(dest))
        log.info(f"  📎 {filename} → {dest} ({len(data)} bytes)")

    return {
        "msg_id": msg_id,
        "subject": subject,
        "sender": sender,
        "date": date_str,
        "pdfs": pdf_paths,
    }


def mark_processed(service, msg_id: str, label_id: str):
    """Agrega label PROCESSED al mensaje para no reprocesarlo."""
    service.users().messages().modify(
        userId="me", id=msg_id,
        body={"addLabelIds": [label_id]},
    ).execute()


# ── Main ────────────────────────────────────────────────────────────────────
def fetch_once(dry_run: bool = False) -> dict:
    """Lee inbox una vez. Devuelve resumen."""
    creds = get_credentials()
    service = build("gmail", "v1", credentials=creds)

    label_id = get_or_create_label(service, PROCESSED_LABEL)
    msg_ids = list_unprocessed_messages(service, label_id)

    log.info(f"Mensajes pendientes: {len(msg_ids)}")

    results = []
    for msg_id in msg_ids:
        try:
            data = download_pdfs(service, msg_id)
            if not data["pdfs"]:
                log.warning(f"⚠️  {msg_id} no tiene PDFs — skip")
                continue
            log.info(f"✅ {msg_id} | from={data['sender'][:50]} | pdfs={len(data['pdfs'])}")
            results.append(data)
            if not dry_run:
                mark_processed(service, msg_id, label_id)
        except HttpError as e:
            log.error(f"❌ Error procesando {msg_id}: {e}")

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "processed": len(results),
        "messages": results,
    }


def main():
    parser = argparse.ArgumentParser(description="GTL Gmail Inbox Fetcher")
    parser.add_argument("--once", action="store_true", help="Corre 1 vez y sale")
    parser.add_argument("--watch", action="store_true", help="Loop cada 5 min")
    parser.add_argument("--dry-run", action="store_true", help="No marca como procesado")
    parser.add_argument("--interval", type=int, default=300, help="Intervalo watch (s)")
    args = parser.parse_args()

    if not args.once and not args.watch:
        args.once = True

    if args.once:
        result = fetch_once(dry_run=args.dry_run)
        print(json.dumps(result, indent=2, default=str))
        return

    if args.watch:
        import time
        log.info(f"Watch mode: polling cada {args.interval}s")
        while True:
            try:
                fetch_once(dry_run=args.dry_run)
            except Exception as e:
                log.error(f"Error en loop: {e}", exc_info=True)
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
