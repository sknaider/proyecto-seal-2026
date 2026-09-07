"""Tests for seal/worktree.py.

Run:
    python3 -m pytest seal/tests/test_worktree.py -v

Creates real temporary git repos — no mocks for git commands.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from seal.worktree import atomic_edit, list_worktrees, prune_worktrees


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_repo(tmp: Path, files: dict[str, str] | None = None) -> Path:
    """Create a minimal git repo with an initial commit."""
    repo = tmp / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@seal"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "SEAL Test"], cwd=repo, check=True, capture_output=True)

    for rel, content in (files or {"hello.py": "x = 1\n"}).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        subprocess.run(["git", "add", rel], cwd=repo, check=True, capture_output=True)

    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


# ── atomic_edit ───────────────────────────────────────────────────────────────

class AtomicEditTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_edit_applies_to_original(self):
        repo = _make_repo(self._tmp, {"src/agent.py": "x = 1\n"})
        with atomic_edit(repo, "src/agent.py") as p:
            p.write_text("x = 42\n")
        self.assertEqual((repo / "src/agent.py").read_text(), "x = 42\n")

    def test_exception_leaves_original_untouched(self):
        repo = _make_repo(self._tmp, {"src/agent.py": "original\n"})
        with self.assertRaises(ValueError):
            with atomic_edit(repo, "src/agent.py") as p:
                p.write_text("modified\n")
                raise ValueError("intentional")
        self.assertEqual((repo / "src/agent.py").read_text(), "original\n")

    def test_no_change_is_noop(self):
        repo = _make_repo(self._tmp, {"noop.py": "stable\n"})
        with atomic_edit(repo, "noop.py") as p:
            pass  # no write
        self.assertEqual((repo / "noop.py").read_text(), "stable\n")

    def test_worktree_dir_removed_after_success(self):
        repo = _make_repo(self._tmp, {"clean.py": "a = 1\n"})
        captured: list[Path] = []
        with atomic_edit(repo, "clean.py") as p:
            captured.append(p.parent)  # /tmp/seal_wt_<uid>
            p.write_text("a = 2\n")
        self.assertFalse(captured[0].exists())

    def test_worktree_dir_removed_after_exception(self):
        repo = _make_repo(self._tmp, {"err.py": "old\n"})
        captured: list[Path] = []
        try:
            with atomic_edit(repo, "err.py") as p:
                captured.append(p.parent)
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertFalse(captured[0].exists())

    def test_new_file_created_in_repo(self):
        repo = _make_repo(self._tmp, {"existing.py": "e = 1\n"})
        with atomic_edit(repo, "new_file.py") as p:
            p.write_text("fresh = True\n")
        self.assertTrue((repo / "new_file.py").exists())
        self.assertEqual((repo / "new_file.py").read_text(), "fresh = True\n")

    def test_multiline_edit(self):
        original = "line1\nline2\nline3\n"
        repo = _make_repo(self._tmp, {"multi.py": original})
        with atomic_edit(repo, "multi.py") as p:
            p.write_text("line1\nLINE2_MODIFIED\nline3\n")
        self.assertIn("LINE2_MODIFIED", (repo / "multi.py").read_text())

    def test_nested_path_edit(self):
        repo = _make_repo(self._tmp, {"a/b/c.py": "deep = True\n"})
        with atomic_edit(repo, "a/b/c.py") as p:
            p.write_text("deep = False\n")
        self.assertEqual((repo / "a/b/c.py").read_text(), "deep = False\n")

    def test_not_a_repo_raises(self):
        not_repo = self._tmp / "not_a_repo"
        not_repo.mkdir()
        with self.assertRaises(subprocess.CalledProcessError):
            with atomic_edit(not_repo, "file.py") as p:
                pass

    def test_concurrent_edits_different_files(self):
        repo = _make_repo(self._tmp, {"f1.py": "a = 1\n", "f2.py": "b = 2\n"})
        from contextlib import ExitStack
        with ExitStack() as stack:
            p1 = stack.enter_context(atomic_edit(repo, "f1.py"))
            p2 = stack.enter_context(atomic_edit(repo, "f2.py"))
            p1.write_text("a = 10\n")
            p2.write_text("b = 20\n")
        self.assertEqual((repo / "f1.py").read_text(), "a = 10\n")
        self.assertEqual((repo / "f2.py").read_text(), "b = 20\n")


# ── list_worktrees ────────────────────────────────────────────────────────────

class ListWorktreesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_returns_list_for_valid_repo(self):
        repo = _make_repo(self._tmp)
        wts = list_worktrees(repo)
        self.assertIsInstance(wts, list)
        self.assertGreaterEqual(len(wts), 1)

    def test_main_worktree_in_list(self):
        repo = _make_repo(self._tmp)
        wts = list_worktrees(repo)
        paths = [w["path"] for w in wts]
        self.assertIn(str(repo), paths)

    def test_returns_empty_for_non_repo(self):
        not_repo = self._tmp / "empty"
        not_repo.mkdir()
        self.assertEqual(list_worktrees(not_repo), [])

    def test_each_entry_has_expected_keys(self):
        repo = _make_repo(self._tmp)
        for wt in list_worktrees(repo):
            self.assertIn("path", wt)
            self.assertIn("head", wt)
            self.assertIn("branch", wt)
            self.assertIn("bare", wt)


# ── prune_worktrees ───────────────────────────────────────────────────────────

class PruneWorktreesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_prune_returns_int(self):
        repo = _make_repo(self._tmp)
        result = prune_worktrees(repo)
        self.assertIsInstance(result, int)
        self.assertGreaterEqual(result, 0)

    def test_prune_non_repo_returns_zero(self):
        not_repo = self._tmp / "nr"
        not_repo.mkdir()
        self.assertEqual(prune_worktrees(not_repo), 0)


if __name__ == "__main__":
    unittest.main()
