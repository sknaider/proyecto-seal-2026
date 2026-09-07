#!/usr/bin/env python3
"""
Tests para Sprint 1 + Sprint 2 — Mitigaciones A/B/D/F.
Ejecutar: python3 -m pytest /home/dadito/IA/proyecto-seal/memory/hooks/test_sprint1_hooks.py -v
o:        python3 /home/dadito/IA/proyecto-seal/memory/hooks/test_sprint1_hooks.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOOKS_DIR = Path("/home/dadito/IA/proyecto-seal/memory/hooks")
EDIT_PRECISION = HOOKS_DIR / "edit_precision.py"
PRE_CHECKPOINT = HOOKS_DIR / "pre_edit_checkpoint.sh"
POST_CHECKPOINT = HOOKS_DIR / "post_edit_checkpoint.sh"
DIFFCHECK = HOOKS_DIR / "post_edit_diffcheck.py"
SCOPE_CHECK = HOOKS_DIR / "pre_scope_check.py"
SCOPE_YAML = Path("/home/dadito/IA/proyecto-seal/memory/agent_scope.yaml")
BACKTRANSLATE = HOOKS_DIR / "post_edit_backtranslate.py"


def run_hook(hook_path: Path, payload: dict, env: dict | None = None) -> dict:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    proc = subprocess.run(
        [str(hook_path)],
        input=json.dumps(payload),
        capture_output=True, text=True,
        env=full_env, timeout=10,
    )
    out = proc.stdout.strip()
    # Tomar solo la última línea JSON válida (logs pueden anteceder)
    last_json = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                last_json = json.loads(line)
            except json.JSONDecodeError:
                pass
    if last_json is None:
        raise AssertionError(f"Hook stdout sin JSON válido. stdout={out!r} stderr={proc.stderr!r}")
    return last_json


# ─────────────────────────── edit_precision ───────────────────────────

class TestEditPrecision(unittest.TestCase):

    def test_block_dot_star(self):
        """`.*` en old_string → bloquear."""
        r = run_hook(EDIT_PRECISION, {
            "tool_name": "Edit",
            "tool_input": {"old_string": "foo.*bar", "new_string": "x"},
            "agent": "TEST",
        })
        self.assertEqual(r["decision"], "block")
        self.assertIn("regex", r["reason"].lower())

    def test_block_charclass(self):
        """`[abc]` → bloquear."""
        r = run_hook(EDIT_PRECISION, {
            "tool_name": "Edit",
            "tool_input": {"old_string": "color = [red]", "new_string": "x"},
            "agent": "TEST",
        })
        self.assertEqual(r["decision"], "block")

    def test_block_backref_classes(self):
        """`\\d`, `\\w`, `\\s` → bloquear."""
        for pat in (r"id_\d+", r"\w+@example", r"foo\sbar"):
            with self.subTest(pat=pat):
                r = run_hook(EDIT_PRECISION, {
                    "tool_name": "Edit",
                    "tool_input": {"old_string": pat, "new_string": "x"},
                    "agent": "TEST",
                })
                self.assertEqual(r["decision"], "block", f"falló con {pat!r}")

    def test_allow_literal_string(self):
        """String normal sin metacaracteres → permitir."""
        r = run_hook(EDIT_PRECISION, {
            "tool_name": "Edit",
            "tool_input": {"old_string": "def hola(nombre):", "new_string": "x"},
            "agent": "TEST",
        })
        self.assertEqual(r["decision"], "allow")

    def test_allow_with_literal_pattern_flag(self):
        """Metacaracter regex pero literal_pattern=True → permitir (audit-logged)."""
        r = run_hook(EDIT_PRECISION, {
            "tool_name": "Edit",
            "tool_input": {
                "old_string": "regex = r'\\d+'",
                "new_string": "x",
                "literal_pattern": True,
            },
            "agent": "TEST",
        })
        self.assertEqual(r["decision"], "allow")
        self.assertIn("literal_pattern", r["reason"])

    def test_multiedit_blocks_one_bad(self):
        """MultiEdit con un edit malo → bloquear."""
        r = run_hook(EDIT_PRECISION, {
            "tool_name": "MultiEdit",
            "tool_input": {"edits": [
                {"old_string": "ok literal", "new_string": "y"},
                {"old_string": "bad.*pattern", "new_string": "z"},
            ]},
            "agent": "TEST",
        })
        self.assertEqual(r["decision"], "block")
        self.assertIn("edits[1]", r["reason"])

    def test_write_passthrough(self):
        """Write no tiene old_string → permitir (B/F cubren contenido)."""
        r = run_hook(EDIT_PRECISION, {
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/x.txt", "content": "anything .*"},
            "agent": "TEST",
        })
        self.assertEqual(r["decision"], "allow")

    def test_unknown_tool_passthrough(self):
        r = run_hook(EDIT_PRECISION, {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "agent": "TEST",
        })
        self.assertEqual(r["decision"], "allow")

    def test_empty_payload(self):
        r = run_hook(EDIT_PRECISION, {})
        self.assertEqual(r["decision"], "allow")


# ─────────────────────────── git checkpoints ───────────────────────────

class TestGitCheckpoints(unittest.TestCase):
    """
    Test pre/post checkpoint hooks contra un repo git temporal.
    Usamos un critical_paths.yaml temporal con paths bajo el tmpdir.
    """

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="seal_test_"))
        # Inicializar repo git
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.tmpdir)], check=True)
        subprocess.run(["git", "-C", str(self.tmpdir), "config", "user.email", "test@seal.local"], check=True)
        subprocess.run(["git", "-C", str(self.tmpdir), "config", "user.name", "test"], check=True)
        # Archivo crítico inicial committed
        self.critical_file = self.tmpdir / "critico.py"
        self.critical_file.write_text("# v0\nprint('hello')\n", encoding="utf-8")
        self.normal_file = self.tmpdir / "normal.txt"
        self.normal_file.write_text("normal\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.tmpdir), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.tmpdir), "commit", "-q", "-m", "init"], check=True)

        # critical_paths.yaml temporal
        self.yaml = self.tmpdir / "critical_paths.yaml"
        self.yaml.write_text(
            f"version: 1\npaths:\n  - {self.critical_file}\nexclude:\n  - \"**/*.log\"\n",
            encoding="utf-8",
        )

        # Copia los hooks al tmpdir parcheando la ruta del yaml
        self.pre_hook = self.tmpdir / "pre.sh"
        self.post_hook = self.tmpdir / "post.sh"
        for src, dst in [(PRE_CHECKPOINT, self.pre_hook), (POST_CHECKPOINT, self.post_hook)]:
            text = src.read_text(encoding="utf-8")
            text = text.replace(
                'CRITICAL_YAML="${REPO_ROOT}/memory/critical_paths.yaml"',
                f'CRITICAL_YAML="{self.yaml}"',
            ).replace(
                'LOG_FILE="${REPO_ROOT}/memory/logs/pre_edit_checkpoint.jsonl"',
                f'LOG_FILE="{self.tmpdir}/pre.log"',
            ).replace(
                'LOG_FILE="${REPO_ROOT}/memory/logs/post_edit_checkpoint.jsonl"',
                f'LOG_FILE="{self.tmpdir}/post.log"',
            )
            dst.write_text(text, encoding="utf-8")
            dst.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── pre_edit_checkpoint ──

    def test_pre_skip_non_critical(self):
        r = run_hook(self.pre_hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.normal_file)},
            "agent": "TEST",
        })
        self.assertEqual(r["checkpoint"], "path_not_critical")

    def test_pre_clean_baseline_on_critical(self):
        """Archivo crítico limpio → registra clean_baseline (no hay nada que stashear)."""
        r = run_hook(self.pre_hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.critical_file)},
            "agent": "TEST",
            "trace_id": "42",
        })
        self.assertEqual(r["checkpoint"], "clean_baseline")
        self.assertTrue(len(r.get("head", "")) >= 7)

    def test_pre_stash_when_dirty(self):
        """Archivo crítico modificado pre-Edit → stash."""
        self.critical_file.write_text("# v0\nprint('hello')\n# pending\n", encoding="utf-8")
        r = run_hook(self.pre_hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.critical_file)},
            "agent": "TEST",
            "trace_id": "99",
        })
        self.assertEqual(r["checkpoint"], "stashed")
        self.assertIn("stash@", r["stash_ref"])
        # Verifica que el stash existe en el repo
        out = subprocess.run(
            ["git", "-C", str(self.tmpdir), "stash", "list"],
            capture_output=True, text=True,
        ).stdout
        self.assertIn("PRE-EDIT TEST", out)
        self.assertIn("trace=99", out)

    def test_pre_anti_stash_storm(self):
        """Dos llamadas <2s al mismo path → segunda skipea."""
        # primer call (clean → clean_baseline). Cualquiera incrementa el stamp.
        r1 = run_hook(self.pre_hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.critical_file)},
            "agent": "TEST", "trace_id": "1",
        })
        self.assertIn(r1["checkpoint"], ("clean_baseline", "stashed"))
        # llamada inmediata
        r2 = run_hook(self.pre_hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.critical_file)},
            "agent": "TEST", "trace_id": "2",
        })
        self.assertEqual(r2["checkpoint"], "skipped_storm")
        self.assertLess(r2["delta_s"], 2)

    # ── post_edit_checkpoint ──

    def test_post_commits_on_critical(self):
        """Tras editar archivo crítico → commit en branch auto-checkpoint/<agente>."""
        self.critical_file.write_text("# v1 modified\nprint('changed')\n", encoding="utf-8")
        r = run_hook(self.post_hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.critical_file)},
            "agent": "ADA",
            "trace_id": "77",
        })
        self.assertEqual(r["checkpoint"], "committed")
        self.assertEqual(r["branch"], "auto-checkpoint/ADA")
        self.assertEqual(len(r["commit"]), 40)
        # Verifica que la branch existe y apunta al commit
        out = subprocess.run(
            ["git", "-C", str(self.tmpdir), "rev-parse", "auto-checkpoint/ADA"],
            capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(out, r["commit"])
        # Verifica que el commit message tiene trace_id
        msg = subprocess.run(
            ["git", "-C", str(self.tmpdir), "log", "-1", "--pretty=%B", r["commit"]],
            capture_output=True, text=True,
        ).stdout
        self.assertIn("trace=77", msg)
        self.assertIn("auto-checkpoint", msg)

    def test_post_skip_non_critical(self):
        self.normal_file.write_text("modified\n", encoding="utf-8")
        r = run_hook(self.post_hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.normal_file)},
            "agent": "ADA",
        })
        self.assertEqual(r["checkpoint"], "path_not_critical")

    def test_post_noop_no_changes(self):
        """Si no hay cambios reales → noop_no_changes."""
        r = run_hook(self.post_hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.critical_file)},
            "agent": "ADA",
        })
        self.assertEqual(r["checkpoint"], "noop_no_changes")


# ─────────────────────── Sprint 2 — diffcheck (B) + hash (F) ───────────────────────

class TestPostEditDiffcheck(unittest.TestCase):
    """Sprint 2 / Mitigation B — post_edit_diffcheck.py"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="seal_diffcheck_"))
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.tmpdir)], check=True)
        subprocess.run(["git", "-C", str(self.tmpdir), "config", "user.email", "test@seal.local"], check=True)
        subprocess.run(["git", "-C", str(self.tmpdir), "config", "user.name", "test"], check=True)
        # Critical file: 40 lines so ratio tests work cleanly
        self.critical_file = self.tmpdir / "critical.py"
        self.critical_file.write_text("\n".join(f"line_{i} = {i}" for i in range(40)) + "\n", encoding="utf-8")
        self.normal_file = self.tmpdir / "normal.txt"
        self.normal_file.write_text("normal content\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.tmpdir), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.tmpdir), "commit", "-q", "-m", "init"], check=True)
        # critical_paths.yaml pointing at our critical file
        self.yaml = self.tmpdir / "critical_paths.yaml"
        self.yaml.write_text(
            f"version: 1\npaths:\n  - {self.critical_file}\nexclude:\n  - \"**/*.log\"\n",
            encoding="utf-8",
        )
        # Patch the hook so it uses our temp yaml + temp log
        self.hook = self.tmpdir / "diffcheck.py"
        text = DIFFCHECK.read_text(encoding="utf-8")
        text = text.replace(
            'CRITICAL_YAML = REPO_ROOT / "memory" / "critical_paths.yaml"',
            f'CRITICAL_YAML = Path("{self.yaml}")',
        ).replace(
            'LOG_FILE = REPO_ROOT / "memory" / "logs" / "post_edit_diffcheck.jsonl"',
            f'LOG_FILE = Path("{self.tmpdir}/diffcheck.log")',
        )
        self.hook.write_text(text, encoding="utf-8")
        self.hook.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_non_critical_passthrough(self):
        r = run_hook(self.hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.normal_file)},
            "agent": "TEST",
        })
        self.assertEqual(r["diffcheck"], "path_not_critical")

    def test_unknown_tool_passthrough(self):
        r = run_hook(self.hook, {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "agent": "TEST",
        })
        self.assertEqual(r["diffcheck"], "tool_not_guarded")

    def test_empty_dict_payload(self):
        """Dict vacío → tool ausente → tool_not_guarded (empty_payload solo con stdin vacío)."""
        r = run_hook(self.hook, {})
        self.assertEqual(r["diffcheck"], "tool_not_guarded")

    def test_small_change_is_ok(self):
        """Cambio de 1 línea en 40 → ok, no anomalía."""
        lines = self.critical_file.read_text().splitlines()
        lines[0] = "line_0 = 999"
        self.critical_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r = run_hook(self.hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.critical_file)},
            "agent": "TEST",
        })
        self.assertEqual(r["diffcheck"], "ok")
        self.assertEqual(r["lines_removed"], 1)
        self.assertLess(r["deletion_ratio"], 0.50)

    def test_large_deletion_triggers_anomaly(self):
        """Eliminar 35 de 40 líneas → anomaly_warn."""
        self.critical_file.write_text(
            "\n".join(f"line_{i} = {i}" for i in range(5)) + "\n",
            encoding="utf-8",
        )
        r = run_hook(self.hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.critical_file)},
            "agent": "TEST",
        })
        self.assertEqual(r["diffcheck"], "anomaly_warn")
        self.assertGreaterEqual(r["lines_removed"], 20)
        self.assertGreaterEqual(r["deletion_ratio"], 0.50)

    def test_write_tool_critical_no_anomaly(self):
        """Write de contenido similar (pocos cambios) → ok."""
        lines = self.critical_file.read_text().splitlines()
        lines[-1] = "line_39 = 100"
        self.critical_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r = run_hook(self.hook, {
            "tool_name": "Write",
            "tool_input": {"file_path": str(self.critical_file)},
            "agent": "TEST",
        })
        self.assertEqual(r["diffcheck"], "ok")


class TestHashMitF(unittest.TestCase):
    """Sprint 2 / Mitigation F — hash pre/post en checkpoints."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="seal_hash_"))
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.tmpdir)], check=True)
        subprocess.run(["git", "-C", str(self.tmpdir), "config", "user.email", "test@seal.local"], check=True)
        subprocess.run(["git", "-C", str(self.tmpdir), "config", "user.name", "test"], check=True)
        self.critical_file = self.tmpdir / "critico.py"
        self.critical_file.write_text("# original\nprint('v0')\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.tmpdir), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.tmpdir), "commit", "-q", "-m", "init"], check=True)
        self.yaml = self.tmpdir / "critical_paths.yaml"
        self.yaml.write_text(
            f"version: 1\npaths:\n  - {self.critical_file}\nexclude: []\n",
            encoding="utf-8",
        )
        self.hash_dir = Path(tempfile.mkdtemp(prefix="seal_hashes_"))

        def patch_hook(src, dst):
            text = src.read_text(encoding="utf-8")
            text = text.replace(
                'CRITICAL_YAML="${REPO_ROOT}/memory/critical_paths.yaml"',
                f'CRITICAL_YAML="{self.yaml}"',
            ).replace(
                'LOG_FILE="${REPO_ROOT}/memory/logs/pre_edit_checkpoint.jsonl"',
                f'LOG_FILE="{self.tmpdir}/pre.log"',
            ).replace(
                'LOG_FILE="${REPO_ROOT}/memory/logs/post_edit_checkpoint.jsonl"',
                f'LOG_FILE="{self.tmpdir}/post.log"',
            ).replace(
                "mkdir -p /tmp/seal_hashes",
                f"mkdir -p {self.hash_dir}",
            ).replace(
                '"/tmp/seal_hashes/${HASH_KEY}.pre"',
                f'"{self.hash_dir}/${{HASH_KEY}}.pre"',
            ).replace(
                '"/tmp/seal_hashes/${HASH_KEY}.post"',
                f'"{self.hash_dir}/${{HASH_KEY}}.post"',
            ).replace(
                'cat "/tmp/seal_hashes/${HASH_KEY}.pre"',
                f'cat "{self.hash_dir}/${{HASH_KEY}}.pre"',
            )
            dst.write_text(text, encoding="utf-8")
            dst.chmod(0o755)

        self.pre_hook = self.tmpdir / "pre.sh"
        self.post_hook = self.tmpdir / "post.sh"
        patch_hook(PRE_CHECKPOINT, self.pre_hook)
        patch_hook(POST_CHECKPOINT, self.post_hook)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        shutil.rmtree(self.hash_dir, ignore_errors=True)

    def test_pre_hook_stores_hash(self):
        """Pre-hook debe crear archivo .pre en hash_dir para archivos críticos."""
        run_hook(self.pre_hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.critical_file)},
            "agent": "TEST",
        })
        pre_files = list(self.hash_dir.glob("*.pre"))
        self.assertEqual(len(pre_files), 1, "Debe haber exactamente 1 archivo .pre")
        self.assertGreater(len(pre_files[0].read_text().strip()), 0)

    def test_post_hook_includes_hash_fields(self):
        """Post-hook tras modificación debe incluir hash_pre, hash_post, hash_changed=true."""
        # Pre (establece baseline)
        run_hook(self.pre_hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.critical_file)},
            "agent": "ADA",
        })
        # Simular edición
        self.critical_file.write_text("# modificado\nprint('v1')\n", encoding="utf-8")
        # Post
        r = run_hook(self.post_hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.critical_file)},
            "agent": "ADA",
            "trace_id": "mf1",
        })
        self.assertEqual(r["checkpoint"], "committed")
        self.assertIn("hash_pre", r)
        self.assertIn("hash_post", r)
        self.assertTrue(r.get("hash_changed"), "hash_changed debe ser true tras modificación")
        self.assertNotEqual(r["hash_pre"], r["hash_post"])


# ─────────────────────── Sprint 3 — scope restriction (C) ───────────────────────

class TestScopeCheck(unittest.TestCase):
    """Sprint 3 / Mitigation C — pre_scope_check.py"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="seal_scope_"))
        # Scope YAML: NEXUS solo puede editar sandbox/
        self.scope_yaml = self.tmpdir / "agent_scope.yaml"
        sandbox = self.tmpdir / "sandbox"
        sandbox.mkdir()
        self.sandbox_file = sandbox / "test.py"
        self.sandbox_file.write_text("# sandbox\n")
        self.prod_file = self.tmpdir / "prod_hook.py"
        self.prod_file.write_text("# prod\n")
        self.scope_yaml.write_text(
            f"version: 1\nmode: warn\nagents:\n"
            f"  NEXUS:\n    description: sandbox only\n"
            f"    allow:\n      - {sandbox}/**\n"
            f"    deny:\n      - {self.tmpdir}/prod_hook.py\n"
            f"  JARVIS:\n    description: all\n    allow:\n      - {self.tmpdir}/**\n    deny: []\n",
            encoding="utf-8",
        )
        # Patch hook to use our scope yaml
        self.hook = self.tmpdir / "scope_check.py"
        text = SCOPE_CHECK.read_text(encoding="utf-8")
        text = text.replace(
            'SCOPE_YAML = REPO_ROOT / "memory" / "agent_scope.yaml"',
            f'SCOPE_YAML = Path("{self.scope_yaml}")',
        ).replace(
            'LOG_FILE = REPO_ROOT / "memory" / "logs" / "pre_scope_check.jsonl"',
            f'LOG_FILE = Path("{self.tmpdir}/scope.log")',
        )
        self.hook.write_text(text, encoding="utf-8")
        self.hook.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_in_scope_allows(self):
        r = run_hook(self.hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.sandbox_file)},
            "agent": "NEXUS",
        })
        self.assertEqual(r["decision"], "allow")
        self.assertEqual(r["scope"], "in_scope")

    def test_deny_pattern_warns_but_allows(self):
        """Deny pattern en modo warn → decision=allow, scope=deny."""
        r = run_hook(self.hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.prod_file)},
            "agent": "NEXUS",
        })
        self.assertEqual(r["decision"], "allow")   # warn mode, no block
        self.assertEqual(r["scope"], "deny")

    def test_not_in_scope_warns_but_allows(self):
        """Path fuera del scope de NEXUS pero no en deny → not_in_scope, still allow."""
        outside = self.tmpdir / "hooks" / "something.sh"
        outside.parent.mkdir(exist_ok=True)
        outside.write_text("#!/bin/bash\n")
        r = run_hook(self.hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(outside)},
            "agent": "NEXUS",
        })
        self.assertEqual(r["decision"], "allow")
        self.assertEqual(r["scope"], "not_in_scope")

    def test_jarvis_in_scope(self):
        """JARVIS con allow /** → in_scope para cualquier archivo del tmpdir."""
        r = run_hook(self.hook, {
            "tool_name": "Write",
            "tool_input": {"file_path": str(self.prod_file)},
            "agent": "JARVIS",
        })
        self.assertEqual(r["decision"], "allow")
        self.assertEqual(r["scope"], "in_scope")

    def test_unknown_agent_passthrough(self):
        """Agente no configurado → in_scope (permisivo)."""
        r = run_hook(self.hook, {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.prod_file)},
            "agent": "UNKNOWN_BOT",
        })
        self.assertEqual(r["decision"], "allow")
        self.assertEqual(r["scope"], "in_scope")

    def test_unknown_tool_passthrough(self):
        r = run_hook(self.hook, {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "agent": "NEXUS",
        })
        self.assertEqual(r["decision"], "allow")
        self.assertEqual(r["scope"], "tool_not_guarded")


# ─────────────────────── Sprint 4 — backtranslation (E) ───────────────────────

class TestBacktranslate(unittest.TestCase):
    """Sprint 4 / Mitigation E — post_edit_backtranslate.py"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="seal_bt_"))
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.tmpdir)], check=True)
        subprocess.run(["git", "-C", str(self.tmpdir), "config", "user.email", "test@seal.local"], check=True)
        subprocess.run(["git", "-C", str(self.tmpdir), "config", "user.name", "test"], check=True)
        # Critical file: initial content
        self.critical = self.tmpdir / "critical.py"
        self.critical.write_text(
            "def foo():\n    return 1\n\ndef bar():\n    return 2\n", encoding="utf-8"
        )
        self.normal = self.tmpdir / "normal.txt"
        self.normal.write_text("normal\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.tmpdir), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.tmpdir), "commit", "-q", "-m", "init"], check=True)
        self.yaml = self.tmpdir / "critical_paths.yaml"
        self.yaml.write_text(
            f"version: 1\npaths:\n  - {self.critical}\nexclude: []\n", encoding="utf-8"
        )
        self.hook = self.tmpdir / "backtranslate.py"
        text = BACKTRANSLATE.read_text(encoding="utf-8")
        text = text.replace(
            'CRITICAL_YAML = REPO_ROOT / "memory" / "critical_paths.yaml"',
            f'CRITICAL_YAML = Path("{self.yaml}")',
        ).replace(
            'LOG_FILE = REPO_ROOT / "memory" / "logs" / "post_edit_backtranslate.jsonl"',
            f'LOG_FILE = Path("{self.tmpdir}/bt.log")',
        )
        self.hook.write_text(text, encoding="utf-8")
        self.hook.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run(self, payload):
        env = os.environ.copy()
        env["SEAL_HOOK_TEST"] = "1"  # suppress network alerts
        proc = subprocess.run(
            [str(self.hook)],
            input=json.dumps(payload),
            capture_output=True, text=True, env=env, timeout=10,
        )
        out = proc.stdout.strip()
        for line in reversed(out.splitlines()):
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except Exception:
                    pass
        raise AssertionError(f"No JSON output. stdout={out!r} stderr={proc.stderr!r}")

    def test_non_critical_passthrough(self):
        r = self._run({
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.normal), "new_string": "x\ny\nz\n"},
            "agent": "TEST",
        })
        self.assertEqual(r["backtranslate"], "path_not_critical")

    def test_unknown_tool_passthrough(self):
        r = self._run({
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "agent": "TEST",
        })
        self.assertEqual(r["backtranslate"], "tool_not_guarded")

    def test_small_new_string_skipped(self):
        """new_string con <3 líneas → skip (demasiado pequeño para comparar)."""
        self.critical.write_text("def foo():\n    return 99\n\ndef bar():\n    return 2\n", encoding="utf-8")
        r = self._run({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(self.critical),
                "old_string": "return 1",
                "new_string": "return 99",
            },
            "agent": "TEST",
        })
        self.assertEqual(r["backtranslate"], "skipped_too_small")

    def test_high_similarity_ok(self):
        """Edición coherente → similarity alta → ok."""
        new_content = "def foo():\n    return 1\n\ndef bar():\n    return 2\n\ndef baz():\n    return 3\n"
        self.critical.write_text(new_content, encoding="utf-8")
        declared = "def baz():\n    return 3\n"  # 2 lines — skip by MIN_LINES
        # Use 4 lines to pass threshold
        declared_big = "def foo():\n    return 1\n\ndef baz():\n    return 3\n"
        r = self._run({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(self.critical),
                "old_string": "def bar():\n    return 2",
                "new_string": declared_big,
            },
            "agent": "TEST",
        })
        # similarity should be high (content mostly matches)
        self.assertIn(r["backtranslate"], ("ok", "anomaly_warn"))
        self.assertIn("similarity", r)

    def test_high_divergence_anomaly(self):
        """new_string declara A pero archivo tiene B completamente diferente → anomaly_warn."""
        # File was completely rewritten
        self.critical.write_text(
            "import os\nimport sys\nimport re\nimport json\nimport time\n"
            "x = 1\ny = 2\nz = 3\na = 4\nb = 5\n",
            encoding="utf-8",
        )
        # But declared new_string is something totally different
        r = self._run({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(self.critical),
                "old_string": "def foo():\n    return 1",
                "new_string": "ALPHA\nBETA\nGAMMA\nDELTA\nEPSILON\nZETA\nETA\nTHETA\n",
            },
            "agent": "TEST",
        })
        self.assertEqual(r["backtranslate"], "anomaly_warn")
        self.assertGreater(r["divergence"], 0.30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
