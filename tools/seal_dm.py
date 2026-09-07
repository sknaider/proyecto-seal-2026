#!/usr/bin/env python3
"""
SEAL Download Manager (SEAL-DM)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IDM-style multi-connection download manager for large model files.
Features:
  - Multi-segment parallel downloads (up to 16 connections)
  - Auto-resume interrupted downloads
  - HuggingFace integration (repo/file → direct URL)
  - Rich progress bars with speed + ETA
  - Download queue with state persistence
  - Integrity verification (file size check)

Usage:
  # Download HF model file
  seal_dm.py hf unsloth/gemma-4-31B-it-GGUF "BF16/gemma-4-31B-it-BF16-00001-of-00002.gguf" -o ~/IA/modelos/llm/gemma4-31B/

  # Download any URL
  seal_dm.py url https://example.com/model.gguf -o ~/IA/modelos/llm/

  # Download multiple HF files from same repo
  seal_dm.py hf unsloth/MiniMax-M2.5-GGUF \
    "UD-Q3_K_XL/MiniMax-M2.5-UD-Q3_K_XL-00001-of-00004.gguf" \
    "UD-Q3_K_XL/MiniMax-M2.5-UD-Q3_K_XL-00002-of-00004.gguf" \
    -o ~/IA/modelos/llm/minimax-m2.5/ -c 16

  # Show active/queued downloads status
  seal_dm.py status

  # Resume all interrupted downloads
  seal_dm.py resume

Author: JARVIS — Team SEAL
"""

import asyncio
import argparse
import json
import os
import sys
import time
import signal
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

import aiohttp
from rich.console import Console
from rich.progress import (
    Progress, BarColumn, DownloadColumn, TransferSpeedColumn,
    TimeRemainingColumn, TextColumn, SpinnerColumn,
)
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich.layout import Layout

console = Console()

# ── Config ───────────────────────────────────────────────────────────────────
STATE_DIR = Path.home() / "IA" / "proyecto-seal" / "tools" / ".seal_dm"
STATE_FILE = STATE_DIR / "downloads.json"
DEFAULT_CONNECTIONS = 8
MAX_CONNECTIONS = 16
CHUNK_SIZE = 1024 * 1024  # 1MB read chunks
MIN_SEGMENT_SIZE = 5 * 1024 * 1024  # 5MB minimum per segment
RETRY_ATTEMPTS = 5
RETRY_DELAY = 3  # seconds
HF_CDN = "https://huggingface.co"

STATE_DIR.mkdir(parents=True, exist_ok=True)

# ── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class Segment:
    index: int
    start: int
    end: int  # inclusive
    downloaded: int = 0
    complete: bool = False
    temp_file: str = ""

@dataclass
class Download:
    url: str
    output_path: str
    filename: str
    total_size: int = 0
    connections: int = DEFAULT_CONNECTIONS
    segments: list = field(default_factory=list)
    status: str = "pending"  # pending, downloading, paused, complete, error
    error: str = ""
    created_at: float = 0
    completed_at: float = 0
    supports_range: bool = True
    hf_repo: str = ""
    hf_file: str = ""

    def to_dict(self):
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d):
        segments = [Segment(**s) for s in d.pop("segments", [])]
        dl = cls(**d)
        dl.segments = segments
        return dl


# ── State Persistence ────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            return {k: Download.from_dict(v) for k, v in data.items()}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def save_state(downloads: dict):
    with open(STATE_FILE, "w") as f:
        json.dump({k: v.to_dict() for k, v in downloads.items()}, f, indent=2)


def download_key(dl: Download) -> str:
    return f"{dl.hf_repo}:{dl.hf_file}" if dl.hf_repo else dl.url


# ── URL Resolution ───────────────────────────────────────────────────────────

def hf_url(repo: str, filepath: str) -> str:
    return f"{HF_CDN}/{repo}/resolve/main/{filepath}"


def get_hf_headers() -> dict:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if not token:
        token_file = Path.home() / ".cache" / "huggingface" / "token"
        if token_file.exists():
            token = token_file.read_text().strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


# ── Core Download Engine ─────────────────────────────────────────────────────

async def get_file_info(session: aiohttp.ClientSession, url: str) -> tuple:
    """Get file size and range support via HEAD request."""
    async with session.head(url, allow_redirects=True) as resp:
        if resp.status == 401:
            raise PermissionError("Auth required. Set HF_TOKEN env var.")
        if resp.status == 404:
            raise FileNotFoundError(f"File not found: {url}")
        resp.raise_for_status()
        size = int(resp.headers.get("Content-Length", 0))
        accept_ranges = resp.headers.get("Accept-Ranges", "none")
        supports_range = accept_ranges == "bytes" and size > 0
        return size, supports_range


def create_segments(total_size: int, num_connections: int, output_dir: str, filename: str) -> list:
    """Split file into download segments."""
    if total_size < MIN_SEGMENT_SIZE:
        num_connections = 1

    segment_size = total_size // num_connections
    if segment_size < MIN_SEGMENT_SIZE:
        num_connections = max(1, total_size // MIN_SEGMENT_SIZE)
        segment_size = total_size // num_connections

    segments = []
    for i in range(num_connections):
        start = i * segment_size
        end = total_size - 1 if i == num_connections - 1 else (i + 1) * segment_size - 1
        temp = os.path.join(output_dir, f".{filename}.part{i:03d}")
        segments.append(Segment(index=i, start=start, end=end, temp_file=temp))
    return segments


async def download_segment(
    session: aiohttp.ClientSession,
    url: str,
    segment: Segment,
    progress: Progress,
    task_id,
    overall_task_id,
    downloads_state: dict,
    dl_key: str,
):
    """Download a single segment with resume support."""
    if segment.complete:
        progress.update(task_id, completed=segment.end - segment.start + 1)
        progress.update(overall_task_id, advance=0)
        return

    # Recovery: if temp file has more data than state recorded, trust the file
    if os.path.exists(segment.temp_file):
        actual_bytes = os.path.getsize(segment.temp_file)
        seg_size = segment.end - segment.start + 1
        # Truncate oversized temp files (corrupt/leftover from previous run with different boundaries)
        if actual_bytes > seg_size:
            with open(segment.temp_file, "r+b") as f:
                f.truncate(seg_size)
            actual_bytes = seg_size
        if actual_bytes > segment.downloaded:
            recovered = actual_bytes - segment.downloaded
            progress.update(task_id, advance=recovered)
            progress.update(overall_task_id, advance=recovered)
            segment.downloaded = actual_bytes
        if actual_bytes >= seg_size:
            segment.complete = True
            segment.downloaded = seg_size
            progress.update(task_id, completed=seg_size)
            return

    current_start = segment.start + segment.downloaded
    headers = {"Range": f"bytes={current_start}-{segment.end}"}
    mode = "ab" if segment.downloaded > 0 else "wb"

    for attempt in range(RETRY_ATTEMPTS):
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status not in (200, 206):
                    raise aiohttp.ClientError(f"HTTP {resp.status}")

                bytes_since_save = 0
                with open(segment.temp_file, mode) as f:
                    async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                        f.write(chunk)
                        chunk_len = len(chunk)
                        segment.downloaded += chunk_len
                        bytes_since_save += chunk_len
                        progress.update(task_id, advance=chunk_len)
                        progress.update(overall_task_id, advance=chunk_len)
                        # Save state every ~50MB so crash recovery works
                        if bytes_since_save >= 50 * 1024 * 1024:
                            if dl_key in downloads_state:
                                save_state(downloads_state)
                            bytes_since_save = 0

                segment.complete = True
                if dl_key in downloads_state:
                    save_state(downloads_state)
                return

        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            if attempt < RETRY_ATTEMPTS - 1:
                wait = RETRY_DELAY * (2 ** attempt)
                progress.console.print(
                    f"  [yellow]Segment {segment.index} retry {attempt+1}/{RETRY_ATTEMPTS} in {wait}s: {e}[/]"
                )
                await asyncio.sleep(wait)
                current_start = segment.start + segment.downloaded
                headers = {"Range": f"bytes={current_start}-{segment.end}"}
                mode = "ab"
            else:
                raise


def merge_segments(segments: list, output_path: str):
    """Merge all segment temp files into final file."""
    with open(output_path, "wb") as out:
        for seg in sorted(segments, key=lambda s: s.index):
            seg_size = seg.end - seg.start + 1
            written = 0
            with open(seg.temp_file, "rb") as inp:
                while written < seg_size:
                    to_read = min(CHUNK_SIZE, seg_size - written)
                    chunk = inp.read(to_read)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)


def cleanup_segments(segments: list):
    """Remove temp segment files."""
    for seg in segments:
        try:
            os.remove(seg.temp_file)
        except OSError:
            pass


# ── Main Download Orchestrator ───────────────────────────────────────────────

async def download_file(dl: Download, downloads_state: dict):
    """Orchestrate multi-segment download of a single file."""
    key = download_key(dl)
    output_file = os.path.join(dl.output_path, dl.filename)
    os.makedirs(dl.output_path, exist_ok=True)

    headers = get_hf_headers()
    timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=60)
    connector = aiohttp.TCPConnector(limit=dl.connections + 2, force_close=False)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout, connector=connector) as session:
        # Get file info if not already known
        if dl.total_size == 0:
            console.print(f"  [dim]Resolving file info...[/]")
            dl.total_size, dl.supports_range = await get_file_info(session, dl.url)
            console.print(f"  [dim]Size: {dl.total_size / (1024**3):.2f} GB | Range: {'yes' if dl.supports_range else 'no'}[/]")

        # Create segments if needed
        if not dl.segments:
            if dl.supports_range:
                dl.segments = create_segments(dl.total_size, dl.connections, dl.output_path, dl.filename)
            else:
                dl.segments = create_segments(dl.total_size, 1, dl.output_path, dl.filename)
                console.print("  [yellow]Server doesn't support range requests — single connection[/]")

        dl.status = "downloading"
        downloads_state[key] = dl
        save_state(downloads_state)

        already_downloaded = sum(s.downloaded for s in dl.segments)

        # Progress display
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            "[progress.percentage]{task.percentage:>3.1f}%",
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            refresh_per_second=4,
        ) as progress:
            # Overall task
            overall_id = progress.add_task(
                f"[cyan]{dl.filename}",
                total=dl.total_size,
                completed=already_downloaded,
            )

            # Per-segment tasks
            seg_tasks = []
            for seg in dl.segments:
                seg_size = seg.end - seg.start + 1
                tid = progress.add_task(
                    f"  seg-{seg.index:02d}",
                    total=seg_size,
                    completed=seg.downloaded,
                    visible=len(dl.segments) > 1,
                )
                seg_tasks.append(tid)

            # Launch all segments concurrently
            tasks = []
            for seg, tid in zip(dl.segments, seg_tasks):
                if not seg.complete:
                    tasks.append(
                        download_segment(
                            session, dl.url, seg, progress, tid, overall_id,
                            downloads_state, key,
                        )
                    )

            if tasks:
                await asyncio.gather(*tasks)

        # Merge segments
        console.print(f"  [dim]Merging {len(dl.segments)} segments...[/]")
        merge_segments(dl.segments, output_file)

        # Verify size
        actual_size = os.path.getsize(output_file)
        if actual_size != dl.total_size:
            console.print(f"  [red]SIZE MISMATCH: expected {dl.total_size}, got {actual_size}[/]")
            dl.status = "error"
            dl.error = f"Size mismatch: {actual_size} vs {dl.total_size}"
        else:
            console.print(f"  [green]Verified: {actual_size / (1024**3):.2f} GB[/]")
            dl.status = "complete"
            dl.completed_at = time.time()
            cleanup_segments(dl.segments)

        downloads_state[key] = dl
        save_state(downloads_state)


# ── CLI Commands ─────────────────────────────────────────────────────────────

async def cmd_hf(args):
    """Download files from HuggingFace."""
    downloads_state = load_state()
    repo = args.repo
    files = args.files
    output = os.path.expanduser(args.output)
    conns = min(args.connections, MAX_CONNECTIONS)

    console.print(Panel(
        f"[bold]SEAL Download Manager[/]\n"
        f"Repo: [cyan]{repo}[/]\n"
        f"Files: {len(files)}\n"
        f"Connections: {conns}\n"
        f"Output: {output}",
        title="SEAL-DM",
        border_style="blue",
    ))

    for filepath in files:
        filename = os.path.basename(filepath)
        subdir = os.path.dirname(filepath)
        dest = os.path.join(output, subdir) if subdir else output
        url = hf_url(repo, filepath)

        dl = Download(
            url=url,
            output_path=dest,
            filename=filename,
            connections=conns,
            created_at=time.time(),
            hf_repo=repo,
            hf_file=filepath,
        )

        key = download_key(dl)

        # Check for existing/resumed download
        if key in downloads_state:
            existing = downloads_state[key]
            if existing.status == "complete":
                console.print(f"  [green]Already complete:[/] {filename}")
                continue
            elif existing.status in ("downloading", "paused", "error"):
                console.print(f"  [yellow]Resuming:[/] {filename}")
                dl = existing
                dl.status = "pending"

        console.print(f"\n[bold]Downloading:[/] {filepath}")

        try:
            await download_file(dl, downloads_state)
            if dl.status == "complete":
                elapsed = dl.completed_at - dl.created_at
                speed = dl.total_size / elapsed / (1024 * 1024) if elapsed > 0 else 0
                console.print(f"  [green bold]DONE[/] in {elapsed:.0f}s ({speed:.1f} MB/s avg)")
        except Exception as e:
            dl.status = "error"
            dl.error = str(e)
            downloads_state[key] = dl
            save_state(downloads_state)
            console.print(f"  [red bold]ERROR:[/] {e}")

    console.print(f"\n[bold green]All downloads processed.[/]")


async def cmd_url(args):
    """Download from direct URL."""
    downloads_state = load_state()
    url = args.url
    output = os.path.expanduser(args.output)
    conns = min(args.connections, MAX_CONNECTIONS)
    filename = args.filename or os.path.basename(url).split("?")[0]

    dl = Download(
        url=url,
        output_path=output,
        filename=filename,
        connections=conns,
        created_at=time.time(),
    )

    key = download_key(dl)
    if key in downloads_state and downloads_state[key].status in ("downloading", "paused", "error"):
        dl = downloads_state[key]
        dl.status = "pending"

    console.print(Panel(
        f"[bold]SEAL Download Manager[/]\n"
        f"URL: [cyan]{url}[/]\n"
        f"File: {filename}\n"
        f"Connections: {conns}",
        title="SEAL-DM",
        border_style="blue",
    ))

    try:
        await download_file(dl, downloads_state)
    except Exception as e:
        dl.status = "error"
        dl.error = str(e)
        downloads_state[key] = dl
        save_state(downloads_state)
        console.print(f"[red bold]ERROR:[/] {e}")


def cmd_status(args):
    """Show status of all downloads."""
    downloads = load_state()
    if not downloads:
        console.print("[dim]No downloads tracked.[/]")
        return

    table = Table(title="SEAL-DM Downloads", border_style="blue")
    table.add_column("File", style="cyan", max_width=40)
    table.add_column("Size", justify="right")
    table.add_column("Progress", justify="right")
    table.add_column("Status")
    table.add_column("Segments", justify="center")

    status_style = {
        "complete": "[green]DONE[/]",
        "downloading": "[yellow]ACTIVE[/]",
        "paused": "[blue]PAUSED[/]",
        "error": "[red]ERROR[/]",
        "pending": "[dim]PENDING[/]",
    }

    for key, dl in downloads.items():
        downloaded = sum(s.downloaded for s in dl.segments) if dl.segments else 0
        pct = (downloaded / dl.total_size * 100) if dl.total_size > 0 else 0
        size_gb = dl.total_size / (1024**3) if dl.total_size > 0 else 0
        done_segs = sum(1 for s in dl.segments if s.complete)
        total_segs = len(dl.segments)

        table.add_row(
            dl.filename,
            f"{size_gb:.1f} GB",
            f"{pct:.1f}%",
            status_style.get(dl.status, dl.status),
            f"{done_segs}/{total_segs}",
        )
        if dl.status == "error" and dl.error:
            table.add_row("", "", f"[red]{dl.error[:60]}[/]", "", "")

    console.print(table)


async def cmd_resume(args):
    """Resume all interrupted downloads."""
    downloads = load_state()
    resumable = {k: v for k, v in downloads.items() if v.status in ("paused", "error", "downloading")}

    if not resumable:
        console.print("[dim]Nothing to resume.[/]")
        return

    console.print(f"[bold]Resuming {len(resumable)} download(s)...[/]\n")

    for key, dl in resumable.items():
        dl.status = "pending"
        console.print(f"[bold]Resuming:[/] {dl.filename}")
        try:
            await download_file(dl, downloads)
        except Exception as e:
            dl.status = "error"
            dl.error = str(e)
            downloads[key] = dl
            save_state(downloads)
            console.print(f"  [red]ERROR:[/] {e}")


def cmd_clean(args):
    """Clean completed downloads from state."""
    downloads = load_state()
    before = len(downloads)
    downloads = {k: v for k, v in downloads.items() if v.status != "complete"}
    save_state(downloads)
    cleaned = before - len(downloads)
    console.print(f"Cleaned {cleaned} completed entries. {len(downloads)} remaining.")


# ── Entry Point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SEAL Download Manager — IDM-style multi-connection downloader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # hf command
    hf_p = sub.add_parser("hf", help="Download from HuggingFace")
    hf_p.add_argument("repo", help="HF repo (e.g. unsloth/gemma-4-31B-it-GGUF)")
    hf_p.add_argument("files", nargs="+", help="File paths within repo")
    hf_p.add_argument("-o", "--output", required=True, help="Output directory")
    hf_p.add_argument("-c", "--connections", type=int, default=DEFAULT_CONNECTIONS, help=f"Parallel connections (default {DEFAULT_CONNECTIONS}, max {MAX_CONNECTIONS})")

    # url command
    url_p = sub.add_parser("url", help="Download from direct URL")
    url_p.add_argument("url", help="Direct download URL")
    url_p.add_argument("-o", "--output", required=True, help="Output directory")
    url_p.add_argument("-f", "--filename", help="Override filename")
    url_p.add_argument("-c", "--connections", type=int, default=DEFAULT_CONNECTIONS, help=f"Parallel connections (default {DEFAULT_CONNECTIONS})")

    # status command
    sub.add_parser("status", help="Show download status")

    # resume command
    sub.add_parser("resume", help="Resume interrupted downloads")

    # clean command
    sub.add_parser("clean", help="Clean completed entries from state")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    # Handle signals for graceful shutdown
    def handle_signal(sig, frame):
        console.print("\n[yellow]Interrupted — state saved. Use 'seal_dm.py resume' to continue.[/]")
        sys.exit(0)
    signal.signal(signal.SIGINT, handle_signal)

    if args.command == "status":
        cmd_status(args)
    elif args.command == "clean":
        cmd_clean(args)
    elif args.command == "hf":
        asyncio.run(cmd_hf(args))
    elif args.command == "url":
        asyncio.run(cmd_url(args))
    elif args.command == "resume":
        asyncio.run(cmd_resume(args))


if __name__ == "__main__":
    main()
