#!/usr/bin/env python3
"""
pdf_ingest.py — Pipeline de ingestion de PDFs para seal_documents (Qdrant)
Divide PDFs en chunks, genera embeddings CPU-only, almacena en seal_documents.

Uso:
  python3 pdf_ingest.py archivo.pdf [--titulo "Título del doc"]
  python3 pdf_ingest.py --dir /ruta/directorio/  (procesa todos los PDFs)
  python3 pdf_ingest.py --list  (lista documentos indexados)
"""
import asyncio
import argparse
import hashlib
import sys
import os
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")

import pdfplumber
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

sys.path.insert(0, str(Path(__file__).parent.parent / "memory"))
from embeddings import get_embedding

QDRANT_HOST = "localhost"
QDRANT_PORT = 6333
COLLECTION = "seal_documents"
CHUNK_SIZE = 400    # palabras por chunk
CHUNK_OVERLAP = 50  # palabras de solapamiento


def extract_text(pdf_path: Path) -> list[dict]:
    """Extrae texto de PDF, retorna lista de páginas con metadata."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            text = text.strip()
            if text:
                pages.append({"page": i, "text": text, "total_pages": len(pdf.pages)})
    return pages


def chunk_pages(pages: list[dict], chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[dict]:
    """Divide páginas en chunks de tamaño fijo con solapamiento."""
    chunks = []
    all_words = []

    for page in pages:
        words = page["text"].split()
        for w in words:
            all_words.append((w, page["page"]))

    i = 0
    chunk_idx = 0
    while i < len(all_words):
        chunk_words = all_words[i : i + chunk_size]
        text = " ".join(w for w, _ in chunk_words)
        pages_in_chunk = list(set(p for _, p in chunk_words))
        pages_in_chunk.sort()

        chunks.append({
            "chunk_idx": chunk_idx,
            "text": text,
            "pages": pages_in_chunk,
            "start_page": pages_in_chunk[0] if pages_in_chunk else 0,
        })
        chunk_idx += 1
        i += chunk_size - overlap

    return chunks


async def ingest_pdf(pdf_path: Path, titulo: str | None = None) -> int:
    """Procesa un PDF y lo almacena en Qdrant. Retorna número de chunks."""
    q = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    titulo = titulo or pdf_path.stem
    doc_hash = hashlib.md5(pdf_path.read_bytes()).hexdigest()

    # Verificar si ya está indexado
    existing = q.scroll(
        collection_name=COLLECTION,
        scroll_filter={"must": [{"key": "doc_hash", "match": {"value": doc_hash}}]},
        limit=1,
    )
    if existing[0]:
        print(f"⚠️  '{titulo}' ya está indexado ({len(existing[0])} chunks). Saltando.")
        return 0

    print(f"📄 Procesando: {pdf_path.name}")
    pages = extract_text(pdf_path)
    if not pages:
        print(f"❌ No se pudo extraer texto de {pdf_path.name}")
        return 0

    total_pages = pages[0]["total_pages"]
    chunks = chunk_pages(pages)
    print(f"   {total_pages} páginas → {len(chunks)} chunks")

    points = []
    for chunk in chunks:
        embedding = await get_embedding(chunk["text"])
        point_id = int(hashlib.md5(f"{doc_hash}_{chunk['chunk_idx']}".encode()).hexdigest()[:15], 16) % (2**63)

        points.append(PointStruct(
            id=point_id,
            vector=embedding,
            payload={
                "titulo": titulo,
                "filename": pdf_path.name,
                "doc_hash": doc_hash,
                "chunk_idx": chunk["chunk_idx"],
                "total_chunks": len(chunks),
                "text": chunk["text"],
                "pages": chunk["pages"],
                "start_page": chunk["start_page"],
                "total_pages": total_pages,
                "indexed_at": datetime.now(LIMA_TZ).isoformat(),
            }
        ))

        if len(points) % 10 == 0:
            print(f"   Embedding {len(points)}/{len(chunks)}...", end="\r")

    # Upsert en batch
    q.upsert(collection_name=COLLECTION, points=points)
    print(f"✅ '{titulo}' indexado: {len(chunks)} chunks en seal_documents")
    return len(chunks)


async def search_documents(query: str, limit: int = 5) -> list[dict]:
    """Búsqueda semántica en documentos indexados."""
    q = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    embedding = await get_embedding(query)

    results = q.query_points(
        collection_name=COLLECTION,
        query=embedding,
        limit=limit,
    )

    hits = []
    for r in results.points:
        hits.append({
            "score": round(r.score, 3),
            "titulo": r.payload.get("titulo"),
            "page": r.payload.get("start_page"),
            "text": r.payload.get("text", "")[:300],
        })
    return hits


async def list_documents() -> None:
    """Lista todos los documentos indexados."""
    q = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    info = q.get_collection(COLLECTION)
    print(f"seal_documents: {info.points_count} chunks totales\n")

    results = q.scroll(collection_name=COLLECTION, limit=1000)
    docs: dict[str, dict] = {}
    for point in results[0]:
        h = point.payload.get("doc_hash", "?")
        if h not in docs:
            docs[h] = {
                "titulo": point.payload.get("titulo"),
                "filename": point.payload.get("filename"),
                "total_pages": point.payload.get("total_pages"),
                "chunks": 0,
                "indexed_at": point.payload.get("indexed_at"),
            }
        docs[h]["chunks"] += 1

    for doc in docs.values():
        print(f"  📄 {doc['titulo']} ({doc['filename']}) — {doc['total_pages']} páginas, {doc['chunks']} chunks")
        print(f"     Indexado: {doc['indexed_at'][:10] if doc['indexed_at'] else '?'}")


async def main():
    parser = argparse.ArgumentParser(description="PDF ingestion para SEAL")
    parser.add_argument("pdf", nargs="?", help="Ruta al PDF")
    parser.add_argument("--titulo", help="Título del documento")
    parser.add_argument("--dir", help="Directorio con PDFs")
    parser.add_argument("--list", action="store_true", help="Listar documentos indexados")
    parser.add_argument("--search", help="Buscar en documentos indexados")
    args = parser.parse_args()

    if args.list:
        await list_documents()
    elif args.search:
        results = await search_documents(args.search)
        for r in results:
            print(f"[{r['score']}] {r['titulo']} p.{r['page']}: {r['text'][:150]}...")
    elif args.dir:
        dir_path = Path(args.dir)
        pdfs = list(dir_path.glob("*.pdf"))
        print(f"Encontrados {len(pdfs)} PDFs en {dir_path}")
        total = 0
        for pdf in pdfs:
            total += await ingest_pdf(pdf)
        print(f"\nTotal: {total} chunks indexados")
    elif args.pdf:
        await ingest_pdf(Path(args.pdf), args.titulo)
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
