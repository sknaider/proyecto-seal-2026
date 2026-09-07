#!/usr/bin/env python3
"""file_chunker.py — SEAL utility para dividir archivos grandes en chunks legibles.

Problema que resuelve:
  Claude Code tiene un límite de 32MB por request. Archivos grandes (specs, docs,
  logs, datasets) no pueden leerse completos en una sola llamada. Este tool los
  divide en chunks bien estructurados con índice claro para que ningún agente
  se confunda al leer.

Estrategia por tipo de archivo:
  .md / .txt  → divide en headings (##, ###) o párrafos. Nunca corta a mitad de sección.
  .jsonl      → divide por número de líneas. Cada chunk incluye rango de líneas.
  .json       → si es array raíz, divide el array. Si es objeto, por claves de nivel 1.
  cualquier   → fallback: divide por líneas (safe para cualquier texto).

Output por archivo input.ext:
  input_chunks/
    input_part1.ext          ← chunk 1
    input_part2.ext          ← chunk 2
    ...
    MANIFEST.json            ← índice con metadata de cada chunk

Uso:
  python3 file_chunker.py <archivo> [--max-mb 25] [--output-dir ./chunks]
  python3 file_chunker.py --help
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

DEFAULT_MAX_MB = 25  # Margen seguro bajo el límite de 32MB de Claude API


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _size_mb(text: str) -> float:
    return len(text.encode("utf-8")) / (1024 * 1024)


def _write_chunk(path: Path, content: str, header: str) -> None:
    """Write a chunk file with a clear header block at the top."""
    full_content = header + content
    path.write_text(full_content, encoding="utf-8")


def _make_header(filename: str, part: int, total: int, description: str) -> str:
    """
    Generate a clearly readable chunk header.
    'Bien detallado para que no tenga confusión al leerlo.' — William, 02-may-2026
    """
    sep = "=" * 70
    return (
        f"{sep}\n"
        f"ARCHIVO : {filename}\n"
        f"CHUNK   : Parte {part} de {total}\n"
        f"CONTENIDO: {description}\n"
        f"{sep}\n\n"
    )


# ─── Splitters ─────────────────────────────────────────────────────────────────

def _split_markdown(text: str, max_mb: float) -> list[tuple[str, str]]:
    """
    Split markdown at top-level (##) heading boundaries.
    Returns list of (description, content) tuples.
    Never cuts a section in the middle.
    """
    lines = text.splitlines(keepends=True)
    sections: list[tuple[str, list[str]]] = []
    current_heading = "Introducción"
    current_lines: list[str] = []

    for line in lines:
        if line.startswith("## ") and current_lines:
            sections.append((current_heading, current_lines))
            current_heading = line.strip().lstrip("# ").strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_heading, current_lines))

    # Merge sections into chunks ≤ max_mb
    chunks: list[tuple[str, str]] = []
    current_content = ""
    current_descs: list[str] = []

    for heading, section_lines in sections:
        section_text = "".join(section_lines)
        candidate = current_content + section_text
        if current_content and _size_mb(candidate) > max_mb:
            chunks.append((", ".join(current_descs), current_content))
            current_content = section_text
            current_descs = [heading]
        else:
            current_content = candidate
            current_descs.append(heading)

    if current_content:
        chunks.append((", ".join(current_descs), current_content))

    return chunks


def _split_jsonl(text: str, max_mb: float) -> list[tuple[str, str]]:
    """
    Split JSONL at line boundaries. Each chunk contains complete JSON lines.
    Returns list of (description, content) tuples with line range info.
    """
    lines = [l for l in text.splitlines(keepends=True) if l.strip()]
    total_lines = len(lines)
    chunks: list[tuple[str, str]] = []
    current_lines: list[str] = []
    current_size = 0.0
    chunk_start = 1

    for i, line in enumerate(lines, 1):
        line_size = _size_mb(line)
        if current_lines and current_size + line_size > max_mb:
            content = "".join(current_lines)
            end_line = chunk_start + len(current_lines) - 1
            desc = f"líneas {chunk_start}–{end_line} de {total_lines}"
            chunks.append((desc, content))
            chunk_start = i
            current_lines = [line]
            current_size = line_size
        else:
            current_lines.append(line)
            current_size += line_size

    if current_lines:
        content = "".join(current_lines)
        end_line = chunk_start + len(current_lines) - 1
        desc = f"líneas {chunk_start}–{end_line} de {total_lines}"
        chunks.append((desc, content))

    return chunks


def _split_json(text: str, max_mb: float) -> list[tuple[str, str]]:
    """
    Split JSON: if root is array → split elements; if object → split by top-level keys.
    Falls back to line-split on parse error.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return _split_lines(text, max_mb)

    if isinstance(data, list):
        total = len(data)
        chunks: list[tuple[str, str]] = []
        current: list[Any] = []
        current_size = 0.0
        chunk_start = 0

        for i, item in enumerate(data):
            item_text = json.dumps(item, ensure_ascii=False, indent=2)
            item_size = _size_mb(item_text)
            if current and current_size + item_size > max_mb:
                content = json.dumps(current, ensure_ascii=False, indent=2)
                desc = f"elementos [{chunk_start}..{chunk_start + len(current) - 1}] de {total}"
                chunks.append((desc, content))
                chunk_start = i
                current = [item]
                current_size = item_size
            else:
                current.append(item)
                current_size += item_size

        if current:
            content = json.dumps(current, ensure_ascii=False, indent=2)
            desc = f"elementos [{chunk_start}..{chunk_start + len(current) - 1}] de {total}"
            chunks.append((desc, content))
        return chunks

    elif isinstance(data, dict):
        keys = list(data.keys())
        total = len(keys)
        chunks = []
        current_dict: dict = {}
        current_size = 0.0

        for key in keys:
            item_text = json.dumps({key: data[key]}, ensure_ascii=False, indent=2)
            item_size = _size_mb(item_text)
            if current_dict and current_size + item_size > max_mb:
                content = json.dumps(current_dict, ensure_ascii=False, indent=2)
                desc = f"claves: {list(current_dict.keys())}"
                chunks.append((desc, content))
                current_dict = {key: data[key]}
                current_size = item_size
            else:
                current_dict[key] = data[key]
                current_size += item_size

        if current_dict:
            content = json.dumps(current_dict, ensure_ascii=False, indent=2)
            desc = f"claves: {list(current_dict.keys())}"
            chunks.append((desc, content))
        return chunks

    # Scalar or other: return as-is
    return [("contenido completo", text)]


def _split_lines(text: str, max_mb: float) -> list[tuple[str, str]]:
    """Generic line-based split. Safe fallback for any text format."""
    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    chunks: list[tuple[str, str]] = []
    current_lines: list[str] = []
    current_size = 0.0
    chunk_start = 1

    for i, line in enumerate(lines, 1):
        line_size = _size_mb(line)
        if current_lines and current_size + line_size > max_mb:
            content = "".join(current_lines)
            end_line = chunk_start + len(current_lines) - 1
            desc = f"líneas {chunk_start}–{end_line} de {total_lines}"
            chunks.append((desc, content))
            chunk_start = i
            current_lines = [line]
            current_size = line_size
        else:
            current_lines.append(line)
            current_size += line_size

    if current_lines:
        content = "".join(current_lines)
        end_line = chunk_start + len(current_lines) - 1
        desc = f"líneas {chunk_start}–{end_line} de {total_lines}"
        chunks.append((desc, content))

    return chunks


# ─── Main chunker ──────────────────────────────────────────────────────────────

def chunk_file(
    source_path: Path,
    max_mb: float = DEFAULT_MAX_MB,
    output_dir: Path | None = None,
) -> Path:
    """
    Split source_path into chunks, write to output_dir.
    Returns path to output_dir (where all chunks + MANIFEST.json live).
    """
    if not source_path.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {source_path}")

    text = source_path.read_text(encoding="utf-8", errors="replace")
    total_size_mb = _size_mb(text)

    if total_size_mb <= max_mb:
        print(f"[file_chunker] {source_path.name} ({total_size_mb:.1f}MB) ya está bajo el límite de {max_mb}MB. No requiere división.")
        return source_path.parent

    # Choose splitter based on extension
    ext = source_path.suffix.lower()
    if ext in {".md", ".txt", ".rst"}:
        raw_chunks = _split_markdown(text, max_mb)
    elif ext == ".jsonl":
        raw_chunks = _split_jsonl(text, max_mb)
    elif ext == ".json":
        raw_chunks = _split_json(text, max_mb)
    else:
        raw_chunks = _split_lines(text, max_mb)

    # Output dir
    if output_dir is None:
        output_dir = source_path.parent / f"{source_path.stem}_chunks"
    output_dir.mkdir(parents=True, exist_ok=True)

    total_chunks = len(raw_chunks)
    manifest_entries = []

    print(f"\n[file_chunker] Dividiendo: {source_path.name}")
    print(f"  Tamaño original: {total_size_mb:.1f}MB")
    print(f"  Chunks: {total_chunks} (max {max_mb}MB cada uno)")
    print(f"  Output: {output_dir}/\n")

    for i, (description, content) in enumerate(raw_chunks, 1):
        # Choose extension: .json chunks get .json, others keep original ext
        chunk_ext = ext if ext != ".jsonl" else ".jsonl"
        chunk_name = f"{source_path.stem}_part{i:02d}{chunk_ext}"
        chunk_path = output_dir / chunk_name

        header = _make_header(
            filename=source_path.name,
            part=i,
            total=total_chunks,
            description=description,
        )

        # For JSON files, don't inject text header (would break JSON parsing)
        if ext == ".json":
            chunk_path.write_text(content, encoding="utf-8")
            written_header = f"[JSON chunk — ver MANIFEST.json para contexto]"
        else:
            _write_chunk(chunk_path, content, header)
            written_header = header.strip()

        chunk_size_mb = _size_mb(content)
        manifest_entries.append({
            "part": i,
            "total_parts": total_chunks,
            "file": chunk_name,
            "description": description,
            "size_mb": round(chunk_size_mb, 2),
            "path": str(chunk_path),
        })

        print(f"  ✅ Chunk {i}/{total_chunks}: {chunk_name} ({chunk_size_mb:.2f}MB) — {description[:60]}")

    # Write MANIFEST.json
    manifest = {
        "original_file": str(source_path),
        "original_size_mb": round(total_size_mb, 2),
        "max_chunk_mb": max_mb,
        "total_chunks": total_chunks,
        "format": ext.lstrip("."),
        "instructions": (
            f"Este archivo fue dividido en {total_chunks} partes. "
            f"Leer en orden: part01 → part{total_chunks:02d}. "
            f"Cada parte tiene un encabezado con el rango de contenido. "
            f"El contenido es continuo — no hay solapamiento entre partes."
        ),
        "chunks": manifest_entries,
    }
    manifest_path = output_dir / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n  📋 MANIFEST.json escrito: {manifest_path}")
    print(f"  ✅ División completa — {total_chunks} chunks listos en {output_dir}/")

    return output_dir


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Divide archivos grandes en chunks legibles para Claude Code (límite 32MB).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("archivo", nargs="?", help="Archivo a dividir")
    parser.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB,
                        help=f"Tamaño máximo por chunk en MB (default: {DEFAULT_MAX_MB})")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Directorio de salida (default: <archivo>_chunks/)")
    parser.add_argument("--list-large", type=Path, default=None,
                        help="Escanea un directorio y lista archivos > --max-mb")

    args = parser.parse_args()

    if args.list_large:
        scan_dir = args.list_large
        print(f"\nArchivos > {args.max_mb}MB en {scan_dir}:\n")
        found = False
        for p in sorted(scan_dir.rglob("*")):
            if p.is_file():
                size_mb = p.stat().st_size / (1024 * 1024)
                if size_mb > args.max_mb:
                    print(f"  {size_mb:7.1f}MB  {p}")
                    found = True
        if not found:
            print(f"  (ningún archivo supera {args.max_mb}MB)")
        return

    if not args.archivo:
        parser.print_help()
        sys.exit(0)

    source = Path(args.archivo)
    chunk_file(source, max_mb=args.max_mb, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
