#!/usr/bin/env python3
"""
SEAL Soul — Backup & Restore
Exporta/importa toda el alma a un solo archivo portable.
Llévalo a cualquier máquina con Docker + Python.

Uso:
    python3 soul_backup.py export              → soul_backup_YYYY-MM-DD.sql.gz
    python3 soul_backup.py export --file alma.sql.gz
    python3 soul_backup.py import soul_backup_2026-03-29.sql.gz
    python3 soul_backup.py status              → muestra estadísticas del alma
"""
from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DB_CONTAINER = "seal-memory-db"
DB_USER = "seal"
DB_NAME = "seal_memory"


def run(cmd: list[str], capture=True) -> str:
    r = subprocess.run(cmd, capture_output=capture, text=True)
    if r.returncode != 0:
        print(f"ERROR: {' '.join(cmd)}\n{r.stderr}", file=sys.stderr)
        sys.exit(1)
    return r.stdout if capture else ""


def export_soul(output_file: str | None = None):
    """Export entire soul to a compressed SQL file."""
    if not output_file:
        date = datetime.now().strftime("%Y-%m-%d_%H%M")
        output_file = f"soul_backup_{date}.sql.gz"

    print(f"Exporting soul from {DB_CONTAINER}...")

    # pg_dump inside container
    sql = run(["docker", "exec", DB_CONTAINER, "pg_dump", "-U", DB_USER, DB_NAME,
               "--no-owner", "--no-privileges", "--clean", "--if-exists"])

    # Compress
    output_path = Path(output_file)
    with gzip.open(output_path, "wt", encoding="utf-8") as f:
        f.write(sql)

    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"Soul exported: {output_path} ({size_mb:.2f} MB)")
    print(f"Carry this file to any machine with Docker + Python.")
    print(f"Restore with: python3 soul_backup.py import {output_path}")


def import_soul(input_file: str):
    """Import soul from a compressed SQL file."""
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Importing soul from {input_path}...")

    # Read compressed SQL
    if input_path.suffix == ".gz":
        with gzip.open(input_path, "rt", encoding="utf-8") as f:
            sql = f.read()
    else:
        sql = input_path.read_text()

    # Ensure extensions exist before import
    ext_sql = "CREATE EXTENSION IF NOT EXISTS vector;\nCREATE EXTENSION IF NOT EXISTS timescaledb;\n"

    # Feed SQL into container
    proc = subprocess.run(
        ["docker", "exec", "-i", DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME],
        input=ext_sql + sql, capture_output=True, text=True,
    )

    if proc.returncode != 0:
        print(f"WARNING: Some errors during import (may be normal for clean/if-exists):")
        # Filter out harmless notices
        for line in proc.stderr.split("\n"):
            if line.strip() and "NOTICE" not in line:
                print(f"  {line}")

    print("Soul imported. Verifying...")
    show_status()


def show_status():
    """Show soul statistics."""
    queries = {
        "memories": "SELECT COUNT(*) FROM memories",
        "memories_with_embeddings": "SELECT COUNT(*) FROM memories WHERE embedding IS NOT NULL",
        "events": "SELECT COUNT(*) FROM event_log",
        "rules": "SELECT COUNT(*) FROM rules WHERE active = TRUE",
        "identities": "SELECT COUNT(*) FROM identity",
        "sessions": "SELECT COUNT(*) FROM sessions",
    }

    print(f"\n{'='*50}")
    print(f"  SEAL Soul — Status")
    print(f"{'='*50}")

    for label, sql in queries.items():
        try:
            result = run(["docker", "exec", DB_CONTAINER, "psql", "-U", DB_USER,
                         "-d", DB_NAME, "-t", "-c", sql]).strip()
            print(f"  {label:30s} {result:>6}")
        except SystemExit:
            print(f"  {label:30s} ERROR")

    # Top memories by importance
    top_sql = "SELECT agent, category, importance, LEFT(content, 80) FROM memories ORDER BY importance DESC LIMIT 5"
    try:
        result = run(["docker", "exec", DB_CONTAINER, "psql", "-U", DB_USER,
                      "-d", DB_NAME, "-t", "-c", top_sql]).strip()
        if result:
            print(f"\n  Top memories:")
            for line in result.split("\n"):
                if line.strip():
                    print(f"    {line.strip()}")
    except SystemExit:
        pass

    print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(description="SEAL Soul — Backup & Restore")
    parser.add_argument("action", choices=["export", "import", "status"],
                       help="export: save soul to file | import: load soul from file | status: show stats")
    parser.add_argument("file", nargs="?", help="File path for export/import")
    parser.add_argument("--file", dest="file_flag", help="Alternative: --file path")

    args = parser.parse_args()
    filepath = args.file or args.file_flag

    if args.action == "export":
        export_soul(filepath)
    elif args.action == "import":
        if not filepath:
            print("ERROR: import requires a file path", file=sys.stderr)
            sys.exit(1)
        import_soul(filepath)
    elif args.action == "status":
        show_status()


if __name__ == "__main__":
    main()
