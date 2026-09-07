#!/usr/bin/env python3
"""SEAL Edge Wiki v1 — Obsidian-compatible Markdown vault in ~/.seal/wiki/.

Manages wiki pages: create, update, read, sync with EdgeLayer SQLite.
Vault structure follows spec_edge_layer_v1.md §4.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from edge_layer import EdgeLayer, SEAL_DIR

WIKI_DIR = SEAL_DIR / "wiki"

VAULT_DIRS = ["raw/articles", "raw/notes", "entities", "concepts", "queries"]

SCHEMA_MD = """# SEAL Wiki — Schema

## Convenciones
- `entities/` — páginas por persona, organización o agente
- `concepts/` — páginas por concepto o tema
- `queries/` — respuestas archivadas valiosas
- `raw/articles/` — web clippings (SHA256 frontmatter, no modificar)
- `raw/notes/` — notas del usuario (drop-folder)

## Tag taxonomy
- `#agent` — páginas sobre agentes SEAL
- `#project` — proyectos activos
- `#paper` — referencias académicas
- `#decision` — decisiones arquitecturales
- `#person` — personas del equipo

## Frontmatter estándar
```yaml
---
title: Nombre de la página
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: entity | concept | comparison | query
tags: [tag1, tag2]
sources: [chunk-id-1]
confidence: high | medium | low
---
```
"""


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug.strip("-")[:80]


def _frontmatter(title: str, page_type: str, tags: list, sources: list,
                 created: str, updated: str, confidence: str = "medium") -> str:
    tags_str = json.dumps(tags)
    sources_str = json.dumps(sources)
    return (
        f"---\n"
        f"title: {title}\n"
        f"created: {created}\n"
        f"updated: {updated}\n"
        f"type: {page_type}\n"
        f"tags: {tags_str}\n"
        f"sources: {sources_str}\n"
        f"confidence: {confidence}\n"
        f"---\n\n"
    )


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from body. Returns (meta, body)."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    yaml_block = text[4:end]
    body = text[end + 5:]
    meta: dict = {}
    for line in yaml_block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, body


class WikiManager:
    def __init__(self, wiki_dir: Path = WIKI_DIR, edge: Optional[EdgeLayer] = None):
        self.wiki_dir = wiki_dir
        self.edge = edge or EdgeLayer()
        self._init_vault()

    def _init_vault(self) -> None:
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        for d in VAULT_DIRS:
            (self.wiki_dir / d).mkdir(parents=True, exist_ok=True)
        schema_path = self.wiki_dir / "SCHEMA.md"
        if not schema_path.exists():
            schema_path.write_text(SCHEMA_MD)
        index_path = self.wiki_dir / "index.md"
        if not index_path.exists():
            index_path.write_text("# SEAL Wiki — Index\n\n<!-- entries appended automatically -->\n")

    # ── Page management ──────────────────────────────────────────────────────

    def write_page(self, title: str, body: str, page_type: str = "concept",
                   tags: Optional[list] = None, sources: Optional[list] = None,
                   subdir: str = "concepts",
                   confidence: str = "medium") -> Path:
        """Write (or overwrite) a wiki page. Returns the file path."""
        slug = _slugify(title)
        page_path = self.wiki_dir / subdir / f"{slug}.md"
        today = _today()

        # Preserve created date if file already exists
        created = today
        if page_path.exists():
            existing_meta, _ = _parse_frontmatter(page_path.read_text())
            created = existing_meta.get("created", today)

        fm = _frontmatter(title, page_type, tags or [], sources or [],
                          created=created, updated=today, confidence=confidence)
        page_path.write_text(fm + body)

        # Sync to EdgeLayer SQLite index
        self.edge.upsert_wiki_entry(
            slug=slug, title=title, content=body,
            tags=tags, sources=sources
        )
        self._update_index(slug, title, subdir)
        return page_path

    def read_page(self, slug: str, subdir: Optional[str] = None) -> Optional[dict]:
        """Read a page by slug. Returns {meta, body, path} or None."""
        if subdir:
            path = self.wiki_dir / subdir / f"{slug}.md"
            if path.exists():
                return self._load_page(path, slug)
        # Search all subdirs
        for p in self.wiki_dir.rglob(f"{slug}.md"):
            return self._load_page(p, slug)
        return None

    def _load_page(self, path: Path, slug: str) -> dict:
        text = path.read_text()
        meta, body = _parse_frontmatter(text)
        meta["tags"] = json.loads(meta.get("tags", "[]")) if meta.get("tags") else []
        meta["sources"] = json.loads(meta.get("sources", "[]")) if meta.get("sources") else []
        return {"slug": slug, "meta": meta, "body": body, "path": str(path)}

    def append_log(self, entry: str, agent: str = "SYSTEM") -> None:
        """Append a timestamped entry to the append-only log.md."""
        log_path = self.wiki_dir / "log.md"
        if not log_path.exists():
            log_path.write_text("# SEAL Wiki — Log\n\n")
        timestamp = _now_iso()
        log_path.open("a").write(f"\n## {timestamp} [{agent}]\n\n{entry}\n")

    def list_pages(self, subdir: Optional[str] = None) -> list[dict]:
        """List all pages in vault or a specific subdir."""
        root = self.wiki_dir / subdir if subdir else self.wiki_dir
        pages = []
        for p in sorted(root.rglob("*.md")):
            if p.name in ("SCHEMA.md", "index.md", "log.md"):
                continue
            meta, _ = _parse_frontmatter(p.read_text())
            pages.append({
                "slug": p.stem,
                "title": meta.get("title", p.stem),
                "type": meta.get("type", "?"),
                "updated": meta.get("updated", "?"),
                "path": str(p.relative_to(self.wiki_dir)),
            })
        return pages

    def _update_index(self, slug: str, title: str, subdir: str) -> None:
        index_path = self.wiki_dir / "index.md"
        text = index_path.read_text() if index_path.exists() else "# SEAL Wiki — Index\n\n"
        link = f"- [{title}]({subdir}/{slug}.md)"
        if link not in text:
            index_path.open("a").write(f"{link}\n")


if __name__ == "__main__":
    wiki = WikiManager()
    path = wiki.write_page(
        title="SEAL Edge Layer",
        body="## Overview\n\nEdge layer provides local SQLite cache and Markdown vault for offline operation.\n",
        page_type="concept",
        tags=["#project", "#edge"],
        subdir="concepts",
    )
    print(f"Created: {path}")
    page = wiki.read_page("seal-edge-layer")
    if page:
        print(f"Title: {page['meta'].get('title')}")
    print(f"Pages: {len(wiki.list_pages())}")
