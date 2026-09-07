#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
minisoul_store.py — Admisión de memoria al mini-SOUL LOCAL (el path de ESCRITURA que faltaba).
================================================================================================
Gap cazado por efecto (grep): el mini-SOUL SINCRONIZA (daemon) y RECIBE (central), pero NADA
ESCRIBÍA memorias en el chunks local. Sin esto, el device no puede ALMACENAR su alma localmente.

Este módulo cierra 2 cosas de un tiro:
  1. El path de escritura local: un agente admite una memoria → INSERT a chunks + encola en outbox.
  2. El wiring del cifrado at-rest (FABLE/NEXUS): el `content` se guarda CIFRADO (minisoul_crypto).
     El .db en disco tiene ciphertext, no plaintext → robo de device / mismo-usuario = inútil sin la clave.

La sync-policy decide qué ENCOLA para subir usando los METADATOS (importance/type/scope), NO el
content cifrado. El daemon, con la clave, descifra al leer para sincronizar (transit por mTLS a central).
"""
from __future__ import annotations
import hashlib
import uuid
from datetime import datetime, timezone

from minisoul_crypto import encrypt_field, decrypt_field
from minisoul_sync_policy import should_sync_up


def _chunk_id(agent: str, content: str) -> str:
    """ID content-addressed (igual criterio que el schema): SHA256(agent+content)[:32]."""
    return hashlib.sha256((agent + content).encode("utf-8")).hexdigest()[:32]


def store_memory(conn, agent, category, content, importance, key,
                 scope="private", memory_type=None):
    """Admite una memoria al mini-SOUL local. content se guarda CIFRADO at-rest.
    Encola en outbox si la sync-policy dice que sube. Devuelve el chunk_id."""
    if not isinstance(content, str) or content == "":
        raise ValueError("content vacío")
    cid = _chunk_id(agent, content)
    enc = encrypt_field(content, key)                 # AT-REST: el disco guarda ciphertext
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT OR REPLACE INTO chunks (id,agent,category,content,importance,scope,created_at,source) "
        "VALUES (?,?,?,?,?,?,?, 'edge')",
        (cid, agent, category, enc, int(importance), scope, now))
    # ¿encolar para sync? decide sobre METADATOS, no sobre el content cifrado.
    meta = {"importance": importance, "memory_type": (memory_type or category),
            "scope": scope, "invalid_at": None}
    if should_sync_up(meta):
        conn.execute(
            "INSERT OR IGNORE INTO outbox (chunk_id,agent,entry_type,scope,nonce) "
            "VALUES (?,?, 'chunk', ?, ?)",
            (cid, agent, scope, str(uuid.uuid4())))
    conn.commit()
    return cid


def read_memory(conn, chunk_id, key):
    """Lee y DESCIFRA una memoria del mini-SOUL local. None si no existe.
    Fail-closed: clave equivocada/tamper → excepción (minisoul_crypto), no plaintext parcial."""
    row = conn.execute(
        "SELECT agent,category,content,importance,scope FROM chunks WHERE id=?",
        (chunk_id,)).fetchone()
    if not row:
        return None
    agent, category, enc, importance, scope = row
    return {"id": chunk_id, "agent": agent, "category": category,
            "content": decrypt_field(enc, key), "importance": importance, "scope": scope}


def read_raw_content_on_disk(conn, chunk_id):
    """Devuelve el content TAL CUAL está en el .db (para probar que at-rest NO es plaintext)."""
    row = conn.execute("SELECT content FROM chunks WHERE id=?", (chunk_id,)).fetchone()
    return row[0] if row else None
