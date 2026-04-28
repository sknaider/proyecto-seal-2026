"""SEAL Worktree — atomic file edits via git worktrees.

Provides a context manager that edits a file inside a throw-away git worktree,
then applies the diff back to the original repository atomically.  If the
context raises an exception, the original file is untouched and the worktree
is cleaned up silently.

Usage:
    from seal.worktree import atomic_edit

    with atomic_edit("/path/to/repo", "src/agent.py") as tmp_path:
        text = tmp_path.read_text()
        tmp_path.write_text(text.replace("old_value", "new_value"))
    # original src/agent.py updated if no exception

Concurrent safety: each call uses a unique worktree directory derived from a
UUID, so parallel edits to different (or even the same) file never collide.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Iterator

logger = logging.getLogger("seal.worktree")


# ── public API ────────────────────────────────────────────────────────────────

@contextmanager
def atomic_edit(
    repo_root: str | Path,
    rel_path: str,
) -> Generator[Path, None, None]:
    """Yield a writable copy of *rel_path* inside a temporary git worktree.

    On clean exit the diff is applied back to the original repository.
    On any exception the original file is untouched; the worktree is always
    removed (silently) in the finally block.

    Args:
        repo_root: Absolute path to the git repository root.
        rel_path:  Path of the file to edit, relative to repo_root.

    Yields:
        Path — absolute path to the file copy inside the worktree.

    Raises:
        subprocess.CalledProcessError — if ``git worktree add`` fails
            (e.g. not a git repository).
    """
    repo_root = Path(repo_root).resolve()
    uid = uuid.uuid4().hex[:12]
    wt_dir = Path(f"/tmp/seal_wt_{uid}")

    _create_worktree(repo_root, wt_dir)
    try:
        dst = _stage_file(repo_root, wt_dir, rel_path)
        yield dst
        _apply_changes(repo_root, wt_dir, rel_path, dst)
    finally:
        _remove_worktree(repo_root, wt_dir)


def list_worktrees(repo_root: str | Path) -> list[dict]:
    """Return metadata for all worktrees in *repo_root*.

    Each dict has keys: ``path``, ``head``, ``branch``, ``bare``.
    Returns an empty list if the directory is not a git repository.
    """
    repo_root = Path(repo_root).resolve()
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    worktrees: list[dict] = []
    current: dict = {}
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": line[len("worktree "):], "head": "", "branch": "", "bare": False}
        elif line.startswith("HEAD "):
            current["head"] = line[5:]
        elif line.startswith("branch "):
            current["branch"] = line[7:]
        elif line == "bare":
            current["bare"] = True
    if current:
        worktrees.append(current)
    return worktrees


def prune_worktrees(repo_root: str | Path) -> int:
    """Remove stale worktrees (directories no longer on disk).

    Returns the number of worktrees pruned.
    """
    repo_root = Path(repo_root).resolve()
    before = len(list_worktrees(repo_root))
    try:
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=repo_root,
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return 0
    after = len(list_worktrees(repo_root))
    return max(0, before - after)


# ── internals ─────────────────────────────────────────────────────────────────

def _create_worktree(repo_root: Path, wt_dir: Path) -> None:
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(wt_dir), "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )


def _stage_file(repo_root: Path, wt_dir: Path, rel_path: str) -> Path:
    """Copy current file from repo into worktree; return destination path."""
    src = repo_root / rel_path
    dst = wt_dir / rel_path
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.copy2(src, dst)
    return dst


def _apply_changes(
    repo_root: Path,
    wt_dir: Path,
    rel_path: str,
    dst: Path,
) -> None:
    """Apply diff from worktree back to original repo."""
    diff = subprocess.run(
        ["git", "diff", "--", rel_path],
        cwd=wt_dir,
        capture_output=True,
        text=True,
    )
    patch = diff.stdout

    if patch.strip():
        apply = subprocess.run(
            ["git", "apply", "--"],
            cwd=repo_root,
            input=patch,
            capture_output=True,
            text=True,
        )
        if apply.returncode != 0:
            logger.debug(
                "git apply failed for %s (%s) — falling back to direct copy",
                rel_path,
                apply.stderr.strip(),
            )
            shutil.copy2(dst, repo_root / rel_path)
    else:
        # git diff empty: either no change, or src already differed from HEAD.
        # Copy dst → src to capture any changes the caller made.
        src = repo_root / rel_path
        if dst.exists() and (not src.exists() or dst.read_bytes() != src.read_bytes()):
            shutil.copy2(dst, src)


def _remove_worktree(repo_root: Path, wt_dir: Path) -> None:
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt_dir)],
            cwd=repo_root,
            capture_output=True,
            timeout=10,
        )
    except Exception:
        pass
    if wt_dir.exists():
        shutil.rmtree(wt_dir, ignore_errors=True)
