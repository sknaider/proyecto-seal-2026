"""Tests for seal/profile.py.

Run:
    python3 -m pytest seal/tests/test_profile.py -v

Redirects PROFILES_DIR to a temp directory — no real ~/.seal writes.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import seal.profile as mod
from seal.profile import (
    create,
    env_vars,
    get,
    list_profiles,
    profile_dir,
)


# ── fixture: redirect SEAL storage to tmpdir ──────────────────────────────────

class _ProfileBase(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._orig_home = mod.SEAL_HOME
        self._orig_profiles = mod.PROFILES_DIR
        mod.SEAL_HOME = self._tmp
        mod.PROFILES_DIR = self._tmp / "profiles"

    def tearDown(self):
        mod.SEAL_HOME = self._orig_home
        mod.PROFILES_DIR = self._orig_profiles


# ── profile_dir / name sanitization ──────────────────────────────────────────

class ProfileDirTests(_ProfileBase):
    def test_returns_correct_path(self):
        p = profile_dir("acme_corp")
        self.assertEqual(p, mod.PROFILES_DIR / "acme_corp")

    def test_sanitizes_uppercase(self):
        p = profile_dir("ACME")
        self.assertEqual(p.name, "acme")

    def test_sanitizes_hyphens(self):
        p = profile_dir("my-client")
        self.assertEqual(p.name, "my_client")

    def test_invalid_name_raises(self):
        with self.assertRaises(ValueError):
            profile_dir("!!bad!!")

    def test_name_too_short_raises(self):
        with self.assertRaises(ValueError):
            profile_dir("a")  # min 2 chars

    def test_name_starting_with_digit_raises(self):
        with self.assertRaises(ValueError):
            profile_dir("1abc")


# ── create ────────────────────────────────────────────────────────────────────

class CreateTests(_ProfileBase):
    def test_creates_directory_structure(self):
        pdir = create("client_a")
        self.assertTrue(pdir.is_dir())
        self.assertTrue((pdir / "config.toml").exists())
        self.assertTrue((pdir / "db_url.env").exists())
        self.assertTrue((pdir / "logs").is_dir())

    def test_config_contains_agent_name(self):
        pdir = create("client_b", agent="ADA")
        cfg = (pdir / "config.toml").read_text()
        self.assertIn('name = "ADA"', cfg)

    def test_schema_derived_from_profile_name(self):
        pdir = create("beta_client")
        env = (pdir / "db_url.env").read_text()
        self.assertIn("SEAL_SCHEMA=soul_v3_beta_client", env)

    def test_custom_db_url_used(self):
        pdir = create("custom_db", db_url="postgresql://user:pass@myhost:5432/mydb")
        env = (pdir / "db_url.env").read_text()
        self.assertIn("SEAL_DB_URL=postgresql://user:pass@myhost:5432/mydb", env)

    def test_default_db_url_written(self):
        pdir = create("default_db")
        env = (pdir / "db_url.env").read_text()
        self.assertIn("SEAL_DB_URL=postgresql://seal:REDACTADO@localhost:5433/seal_memory", env)

    def test_duplicate_raises_file_exists(self):
        create("dup")
        with self.assertRaises(FileExistsError):
            create("dup")

    def test_overwrite_replaces_config(self):
        create("to_overwrite", agent="JARVIS")
        pdir = create("to_overwrite", agent="ADA", overwrite=True)
        cfg = (pdir / "config.toml").read_text()
        self.assertIn('name = "ADA"', cfg)

    def test_returns_path_object(self):
        result = create("path_test")
        self.assertIsInstance(result, Path)

    def test_idempotent_logs_subdir(self):
        create("idem")
        pdir = create("idem", overwrite=True)
        self.assertTrue((pdir / "logs").is_dir())


# ── list_profiles ─────────────────────────────────────────────────────────────

class ListProfilesTests(_ProfileBase):
    def test_empty_when_no_profiles(self):
        self.assertEqual(list_profiles(), [])

    def test_lists_created_profiles(self):
        create("alpha")
        create("beta")
        names = [p["name"] for p in list_profiles()]
        self.assertIn("alpha", names)
        self.assertIn("beta", names)

    def test_sorted_alphabetically(self):
        create("zebra")
        create("apple")
        names = [p["name"] for p in list_profiles()]
        self.assertEqual(names, sorted(names))

    def test_each_entry_has_expected_keys(self):
        create("info_test")
        profiles = list_profiles()
        entry = profiles[0]
        self.assertIn("name", entry)
        self.assertIn("path", entry)
        self.assertIn("schema", entry)
        self.assertIn("running", entry)

    def test_schema_in_listing(self):
        create("schema_check")
        profiles = list_profiles()
        self.assertEqual(profiles[0]["schema"], "soul_v3_schema_check")

    def test_running_empty_when_no_lock_files(self):
        create("no_locks")
        profiles = list_profiles()
        self.assertEqual(profiles[0]["running"], [])

    def test_stale_lock_cleaned_during_listing(self):
        create("stale")
        pdir = profile_dir("stale")
        # Write lock with non-existent PID
        (pdir / "jarvis.lock").write_text("999999999")
        profiles = list_profiles()
        self.assertEqual(profiles[0]["running"], [])
        self.assertFalse((pdir / "jarvis.lock").exists())

    def test_live_lock_reported_as_running(self):
        create("live")
        pdir = profile_dir("live")
        # Write current process PID — os.kill(pid, 0) will succeed
        (pdir / "jarvis.lock").write_text(str(os.getpid()))
        profiles = list_profiles()
        self.assertIn("jarvis", profiles[0]["running"])
        (pdir / "jarvis.lock").unlink(missing_ok=True)


# ── get ───────────────────────────────────────────────────────────────────────

class GetTests(_ProfileBase):
    def test_get_existing_profile(self):
        create("get_me")
        info = get("get_me")
        self.assertEqual(info["name"], "get_me")
        self.assertEqual(info["schema"], "soul_v3_get_me")

    def test_get_missing_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            get("ghost")

    def test_get_normalizes_name(self):
        create("upper_test")
        info = get("UPPER_TEST")
        self.assertEqual(info["name"], "upper_test")

    def test_get_path_is_absolute(self):
        create("abs_path")
        info = get("abs_path")
        self.assertTrue(Path(info["path"]).is_absolute())

    def test_get_db_url_default(self):
        create("db_default")
        info = get("db_default")
        self.assertIn("localhost:5433", info["db_url"])


# ── env_vars ──────────────────────────────────────────────────────────────────

class EnvVarsTests(_ProfileBase):
    def test_returns_seal_schema_and_db_url(self):
        create("env_test")
        ev = env_vars("env_test")
        self.assertIn("SEAL_SCHEMA", ev)
        self.assertIn("SEAL_DB_URL", ev)
        self.assertEqual(ev["SEAL_SCHEMA"], "soul_v3_env_test")

    def test_custom_db_url_reflected(self):
        create("custom_env", db_url="postgresql://x:y@z:1234/db")
        ev = env_vars("custom_env")
        self.assertEqual(ev["SEAL_DB_URL"], "postgresql://x:y@z:1234/db")

    def test_missing_profile_raises(self):
        with self.assertRaises(FileNotFoundError):
            env_vars("nonexistent")


if __name__ == "__main__":
    unittest.main()
