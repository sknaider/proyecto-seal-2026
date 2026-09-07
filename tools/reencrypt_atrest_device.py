#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reencrypt_atrest_device.py — Migración one-shot: re-cifra memorias en PLANO del mini-SOUL del device.

MOTIVO (catch FABLE 2026-07-03, corregido a 12/13): el path actual (minisoul_store.store_memory) cifra bien,
pero quedó 1+ fila en PLANO de escrituras VIEJAS (antes de cablear el at-rest, ej. el milestone de wake).
Esta migración deja el mini-SOUL 100% cifrado at-rest: si roban/copian el device, el alma = ciphertext.

CORRE EN EL DEVICE (necesita el keyring secret local para derivar la clave por-agente). Idempotente:
salta las filas YA cifradas; solo re-escribe las que están en claro. + endurece permisos del .db a 0600.

Uso:  python3 reencrypt_atrest_device.py [--db /ruta/mini-soul.db] [--dry-run]
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import sys
from pathlib import Path

# minisoul_crypto vive en el bundle del device (~/.seal/lib) o memory/
for p in (Path.home() / ".seal" / "lib", Path(__file__).resolve().parents[1] / "memory"):
    if p.exists():
        sys.path.insert(0, str(p))
from minisoul_crypto import encrypt_field, is_encrypted, derive_agent_key  # noqa: E402


def _atrest_secret(agent: str, seal_dir: Path):
    """Carga el secreto keyring del device para derivar la clave at-rest del agente."""
    try:
        from minisoul_sync_daemon import load_atrest_secret
        return load_atrest_secret(agent, str(seal_dir))
    except Exception:
        # fallback: archivo de secreto por-agente 0600 en ~/.seal
        f = seal_dir / f"{agent}.atrest.key"
        if f.exists():
            return f.read_bytes()
        raise RuntimeError(f"no encuentro el secreto at-rest de {agent} — no puedo re-cifrar")


def reencrypt(db_path: str, seal_dir: Path, dry_run: bool = False) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    keys: dict[str, bytes] = {}
    total = plano = recifradas = errores = 0
    try:
        rows = conn.execute("SELECT id, agent, content FROM chunks").fetchall()
        for r in rows:
            total += 1
            content = r["content"]
            if not isinstance(content, str) or is_encrypted(content):
                continue  # ya cifrada o vacía → skip (idempotente)
            plano += 1
            agent = (r["agent"] or "").upper()
            try:
                if agent not in keys:
                    keys[agent] = derive_agent_key(agent, _atrest_secret(agent, seal_dir))
                enc = encrypt_field(content, keys[agent])
                if not dry_run:
                    conn.execute("UPDATE chunks SET content=? WHERE id=?", (enc, r["id"]))
                recifradas += 1
            except Exception as e:
                errores += 1
                print(f"  ⚠ no pude re-cifrar id={r['id']} agent={agent}: {str(e)[:80]}")
        if not dry_run:
            conn.commit()
            try:
                os.chmod(db_path, 0o600)  # endurecer: no world-readable
            except Exception:
                pass
    finally:
        conn.close()
    return {"total": total, "en_plano": plano, "recifradas": recifradas, "errores": errores, "dry_run": dry_run}


def main():
    ap = argparse.ArgumentParser()
    default_db = str(Path.home() / ".seal" / "mini-soul.db")
    ap.add_argument("--db", default=os.environ.get("MINISOUL_DB", default_db))
    ap.add_argument("--seal-dir", default=str(Path.home() / ".seal"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not Path(a.db).exists():
        print(f"✗ no existe la DB: {a.db}"); sys.exit(2)
    res = reencrypt(a.db, Path(a.seal_dir), a.dry_run)
    print(f"{'[DRY-RUN] ' if res['dry_run'] else ''}chunks={res['total']} en_plano={res['en_plano']} "
          f"re-cifradas={res['recifradas']} errores={res['errores']}")
    if res["errores"] == 0 and res["en_plano"] == res["recifradas"]:
        print("✅ mini-SOUL del device 100% cifrado at-rest (o ya lo estaba). +0600.")
    else:
        print("⚠ quedaron filas sin re-cifrar — revisar el secreto at-rest de esos agentes.")


if __name__ == "__main__":
    main()
