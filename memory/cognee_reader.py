"""Document readers for cognee_ingest pipeline — PDF, Markdown, TXT."""
from __future__ import annotations

from pathlib import Path


def read_pdf(path: str | Path) -> list[tuple[int, str]]:
    """Read PDF and return list of (page_num, text) tuples."""
    import pdfplumber
    results = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            text = text.strip()
            if text:
                results.append((i, text))
    return results


def read_md(path: str | Path) -> list[tuple[int, str]]:
    """Read Markdown file and return [(1, full_text)]."""
    text = Path(path).read_text(encoding="utf-8", errors="replace").strip()
    return [(1, text)] if text else []


def read_txt(path: str | Path) -> list[tuple[int, str]]:
    """Read plain text file and return [(1, full_text)]."""
    text = Path(path).read_text(encoding="utf-8", errors="replace").strip()
    return [(1, text)] if text else []


def read_document(path: str | Path) -> list[tuple[int, str]]:
    """Auto-detect format and read document. Returns [(page_num, text)]."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return read_pdf(path)
    elif suffix in (".md", ".markdown"):
        return read_md(path)
    elif suffix in (".txt", ".text", ".rst"):
        return read_txt(path)
    else:
        return read_txt(path)
