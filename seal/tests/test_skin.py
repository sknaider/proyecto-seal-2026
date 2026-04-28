"""Contract tests for seal/skin.py

Run:
    python3 -m pytest seal/tests/test_skin.py -v

Or standalone:
    python3 -m unittest seal.tests.test_skin -v
"""
from __future__ import annotations

import curses
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from seal.skin import (
    DEFAULT_SKIN,
    THEMES,
    Skin,
    SkinConfig,
    _hex_to_rgb,
    _is_dark_terminal,
    _parse_toml_minimal,
    _read_skin_config,
    _write_skin_config,
    hex_to_curses,
    load_skin,
    _parse_args,
)


# ---------------------------------------------------------------------------
# Hex parsing
# ---------------------------------------------------------------------------


class HexToRgbTests(unittest.TestCase):
    def test_full_hex(self) -> None:
        self.assertEqual(_hex_to_rgb("#1A3A6B"), (26, 58, 107))

    def test_no_hash(self) -> None:
        self.assertEqual(_hex_to_rgb("FF0000"), (255, 0, 0))

    def test_short_hex(self) -> None:
        r, g, b = _hex_to_rgb("#FFF")
        self.assertEqual((r, g, b), (255, 255, 255))

    def test_black(self) -> None:
        self.assertEqual(_hex_to_rgb("#000000"), (0, 0, 0))

    def test_white(self) -> None:
        self.assertEqual(_hex_to_rgb("#FFFFFF"), (255, 255, 255))

    def test_invalid_raises(self) -> None:
        with self.assertRaises(ValueError):
            _hex_to_rgb("not-a-color")

    def test_case_insensitive(self) -> None:
        self.assertEqual(_hex_to_rgb("#ff0000"), _hex_to_rgb("#FF0000"))


# ---------------------------------------------------------------------------
# hex_to_curses
# ---------------------------------------------------------------------------


class HexToCursesTests(unittest.TestCase):
    def test_returns_int(self) -> None:
        result = hex_to_curses("#1A3A6B")
        self.assertIsInstance(result, int)

    def test_pure_red_returns_red(self) -> None:
        self.assertEqual(hex_to_curses("#FF0000"), curses.COLOR_RED)

    def test_pure_green_returns_green(self) -> None:
        self.assertEqual(hex_to_curses("#00FF00"), curses.COLOR_GREEN)

    def test_pure_blue_returns_blue(self) -> None:
        self.assertEqual(hex_to_curses("#0000FF"), curses.COLOR_BLUE)

    def test_black_returns_black(self) -> None:
        self.assertEqual(hex_to_curses("#000000"), curses.COLOR_BLACK)

    def test_white_returns_white(self) -> None:
        self.assertEqual(hex_to_curses("#FFFFFF"), curses.COLOR_WHITE)

    def test_invalid_returns_white(self) -> None:
        self.assertEqual(hex_to_curses("invalid"), curses.COLOR_WHITE)

    def test_gtl_blue_returns_blue(self) -> None:
        result = hex_to_curses("#1A3A6B")
        self.assertEqual(result, curses.COLOR_BLUE)

    def test_gtl_gold_returns_yellow(self) -> None:
        result = hex_to_curses("#E8A838")
        self.assertEqual(result, curses.COLOR_YELLOW)


# ---------------------------------------------------------------------------
# Predefined themes
# ---------------------------------------------------------------------------


class ThemesTests(unittest.TestCase):
    def test_all_themes_present(self) -> None:
        for name in ("gtl", "hospital", "minimal"):
            self.assertIn(name, THEMES)

    def test_each_theme_is_skin_config(self) -> None:
        for name, cfg in THEMES.items():
            self.assertIsInstance(cfg, SkinConfig)

    def test_gtl_primary_is_dark_blue(self) -> None:
        self.assertEqual(THEMES["gtl"].primary_color, "#1A3A6B")

    def test_hospital_accent_is_green(self) -> None:
        self.assertIn("4C", THEMES["hospital"].accent_color.upper())

    def test_minimal_accent_is_green(self) -> None:
        self.assertEqual(THEMES["minimal"].accent_color, "#00FF00")


# ---------------------------------------------------------------------------
# Skin.from_config
# ---------------------------------------------------------------------------


class SkinFromConfigTests(unittest.TestCase):
    def test_from_gtl_primary_is_blue(self) -> None:
        skin = Skin.from_config(THEMES["gtl"])
        self.assertEqual(skin.primary, curses.COLOR_BLUE)

    def test_from_config_stores_config(self) -> None:
        cfg = SkinConfig(primary_color="#FF0000")
        skin = Skin.from_config(cfg)
        self.assertIs(skin.config, cfg)

    def test_secondary_mapped(self) -> None:
        cfg = SkinConfig(secondary_color="#AAAAAA")
        skin = Skin.from_config(cfg)
        self.assertIsInstance(skin.secondary, int)

    def test_accent_mapped(self) -> None:
        cfg = SkinConfig(accent_color="#00FF00")
        skin = Skin.from_config(cfg)
        self.assertEqual(skin.accent, curses.COLOR_GREEN)


# ---------------------------------------------------------------------------
# Skin.pair (safe fallback — no terminal)
# ---------------------------------------------------------------------------


class SkinPairTests(unittest.TestCase):
    def test_pair_returns_int(self) -> None:
        skin = Skin()
        result = skin.pair("primary")
        self.assertIsInstance(result, int)

    def test_unknown_variant_uses_primary(self) -> None:
        skin = Skin()
        result = skin.pair("nonexistent")
        self.assertIsInstance(result, int)

    def test_all_variants(self) -> None:
        skin = Skin()
        for v in ("primary", "secondary", "accent"):
            self.assertIsInstance(skin.pair(v), int)


# ---------------------------------------------------------------------------
# Terminal detection
# ---------------------------------------------------------------------------


class TerminalDetectionTests(unittest.TestCase):
    def test_dark_terminal(self) -> None:
        with patch.dict(os.environ, {"COLORFGBG": "15;0"}):
            self.assertTrue(_is_dark_terminal())

    def test_light_terminal(self) -> None:
        with patch.dict(os.environ, {"COLORFGBG": "0;15"}):
            self.assertFalse(_is_dark_terminal())

    def test_missing_env_assumes_dark(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "COLORFGBG"}
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(_is_dark_terminal())

    def test_malformed_env_assumes_dark(self) -> None:
        with patch.dict(os.environ, {"COLORFGBG": "notanumber"}):
            self.assertTrue(_is_dark_terminal())


# ---------------------------------------------------------------------------
# TOML parser
# ---------------------------------------------------------------------------


class ParseTomlTests(unittest.TestCase):
    def test_parses_skin_section(self) -> None:
        toml = '[skin]\nprimary_color = "#1A3A6B"\ntheme = "gtl"\n'
        data = _parse_toml_minimal(toml)
        self.assertEqual(data["skin"]["primary_color"], "#1A3A6B")
        self.assertEqual(data["skin"]["theme"], "gtl")

    def test_multiple_sections(self) -> None:
        toml = '[agent]\nname = "JARVIS"\n[skin]\ntheme = "gtl"\n'
        data = _parse_toml_minimal(toml)
        self.assertIn("agent", data)
        self.assertIn("skin", data)

    def test_ignores_comments(self) -> None:
        toml = '# comment\n[skin]\n# another\nprimary_color = "#FFF"\n'
        data = _parse_toml_minimal(toml)
        self.assertEqual(data["skin"]["primary_color"], "#FFF")

    def test_empty_file(self) -> None:
        self.assertEqual(_parse_toml_minimal(""), {})

    def test_single_quotes(self) -> None:
        toml = "[skin]\ntheme = 'minimal'\n"
        data = _parse_toml_minimal(toml)
        self.assertEqual(data["skin"]["theme"], "minimal")


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------


class ConfigIOTests(unittest.TestCase):
    def _seal_home(self, tmp: str) -> Path:
        return Path(tmp) / ".seal"

    def _profile_dir(self, seal_home: Path, profile: str) -> Path:
        d = seal_home / "profiles" / profile
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_read_nonexistent_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = _read_skin_config("ghost", self._seal_home(tmp))
            self.assertIsNone(result)

    def test_write_then_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sh = self._seal_home(tmp)
            self._profile_dir(sh, "myp")
            cfg_in = SkinConfig(
                primary_color="#1A3A6B",
                secondary_color="#4A7FB5",
                accent_color="#E8A838",
                theme="gtl",
            )
            _write_skin_config("myp", cfg_in, sh)
            cfg_out = _read_skin_config("myp", sh)
            self.assertIsNotNone(cfg_out)
            self.assertEqual(cfg_out.primary_color,   "#1A3A6B")
            self.assertEqual(cfg_out.secondary_color, "#4A7FB5")
            self.assertEqual(cfg_out.accent_color,    "#E8A838")
            self.assertEqual(cfg_out.theme,           "gtl")

    def test_write_overwrites_existing_skin_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sh = self._seal_home(tmp)
            self._profile_dir(sh, "p")
            cfg1 = SkinConfig(primary_color="#FF0000", theme="old")
            _write_skin_config("p", cfg1, sh)
            cfg2 = SkinConfig(primary_color="#0000FF", theme="new")
            _write_skin_config("p", cfg2, sh)
            cfg_out = _read_skin_config("p", sh)
            self.assertEqual(cfg_out.primary_color, "#0000FF")
            self.assertEqual(cfg_out.theme, "new")

    def test_write_preserves_other_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sh = self._seal_home(tmp)
            pdir = self._profile_dir(sh, "q")
            config_path = sh / "profiles" / "q" / "config.toml"
            config_path.write_text('[agent]\nname = "ADA"\n')
            _write_skin_config("q", SkinConfig(), sh)
            text = config_path.read_text()
            self.assertIn("[agent]", text)
            self.assertIn("[skin]", text)

    def test_write_creates_profile_dir_if_needed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sh = self._seal_home(tmp)
            _write_skin_config("brand_new", SkinConfig(theme="gtl"), sh)
            cfg = _read_skin_config("brand_new", sh)
            self.assertIsNotNone(cfg)


# ---------------------------------------------------------------------------
# load_skin
# ---------------------------------------------------------------------------


class LoadSkinTests(unittest.TestCase):
    def test_theme_kwarg_overrides_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skin = load_skin("any", theme="gtl",
                             seal_home=Path(tmp) / ".seal")
            self.assertEqual(skin.primary, curses.COLOR_BLUE)

    def test_unknown_theme_kwarg_falls_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # no config file — returns default skin without raising
            skin = load_skin("ghost", theme=None,
                             seal_home=Path(tmp) / ".seal")
            self.assertIsInstance(skin, Skin)

    def test_saved_theme_name_resolves_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sh = Path(tmp) / ".seal"
            _write_skin_config("p", SkinConfig(theme="gtl"), sh)
            skin = load_skin("p", seal_home=sh)
            self.assertEqual(skin.primary, Skin.from_config(THEMES["gtl"]).primary)

    def test_custom_hex_in_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sh = Path(tmp) / ".seal"
            _write_skin_config("p", SkinConfig(
                primary_color="#FF0000", theme="custom"
            ), sh)
            skin = load_skin("p", seal_home=sh)
            self.assertEqual(skin.primary, curses.COLOR_RED)

    def test_returns_skin_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skin = load_skin("ghost", seal_home=Path(tmp) / ".seal")
            self.assertIsInstance(skin, Skin)


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


class ParseArgsTests(unittest.TestCase):
    def test_set_requires_profile(self) -> None:
        with self.assertRaises(SystemExit):
            _parse_args(["set"])

    def test_set_with_theme(self) -> None:
        args = _parse_args(["set", "--profile", "acme", "--theme", "gtl"])
        self.assertEqual(args.command, "set")
        self.assertEqual(args.profile, "acme")
        self.assertEqual(args.theme, "gtl")

    def test_set_with_hex(self) -> None:
        args = _parse_args(["set", "--profile", "p", "--primary", "#1A3A6B"])
        self.assertEqual(args.primary, "#1A3A6B")

    def test_show_command(self) -> None:
        args = _parse_args(["show", "--profile", "acme"])
        self.assertEqual(args.command, "show")

    def test_list_command(self) -> None:
        args = _parse_args(["list"])
        self.assertEqual(args.command, "list")

    def test_show_requires_profile(self) -> None:
        with self.assertRaises(SystemExit):
            _parse_args(["show"])


if __name__ == "__main__":
    unittest.main()
