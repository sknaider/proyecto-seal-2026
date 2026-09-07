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
    BUILT_IN_SKINS,
    AnsiSkin,
    Skin,
    SkinConfig,
    _ansi_fg,
    _hex_to_rgb,
    _is_dark_terminal,
    _parse_skin_yaml,
    _parse_toml_minimal,
    _read_skin_config,
    _write_skin_config,
    hex_to_curses,
    load_ansi_skin,
    load_skin,
    load_skin_yaml,
    skin_to_yaml,
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


# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------

_GTL_YAML = """\
name: client_gtl
branding_name: "SEAL · GTL"
branding_tagline: "GTL Consulting Intelligence"
primary_color: "#1A3A6B"
secondary_color: "#4A7FB5"
accent_color: "#E8A838"
error_color: "#CC3333"
success_color: "#33AA33"
spinner_frames:
  - "⠋"
  - "⠙"
  - "⠹"
  - "⠸"
"""

_MINIMAL_YAML = """\
name: minimal
primary_color: "#FFFFFF"
secondary_color: "#AAAAAA"
accent_color: "#00FF00"
"""


class ParseSkinYamlTests(unittest.TestCase):
    def test_scalars(self) -> None:
        d = _parse_skin_yaml(_GTL_YAML)
        self.assertEqual(d["name"], "client_gtl")
        self.assertEqual(d["primary_color"], "#1A3A6B")
        self.assertEqual(d["branding_name"], "SEAL · GTL")

    def test_spinner_list(self) -> None:
        d = _parse_skin_yaml(_GTL_YAML)
        self.assertIsInstance(d["spinner_frames"], list)
        self.assertEqual(d["spinner_frames"][0], "⠋")
        self.assertEqual(len(d["spinner_frames"]), 4)

    def test_ignores_comments(self) -> None:
        text = "# comment\nname: test\n# another\nprimary_color: \"#000000\"\n"
        d = _parse_skin_yaml(text)
        self.assertEqual(d["name"], "test")
        self.assertNotIn("# comment", d)

    def test_minimal_from_dict_applies_defaults(self) -> None:
        d = _parse_skin_yaml(_MINIMAL_YAML)
        cfg = SkinConfig.from_dict(d)
        self.assertEqual(cfg.branding_name, "SEAL")
        self.assertIsInstance(cfg.spinner_frames, list)
        self.assertGreater(len(cfg.spinner_frames), 0)

    def test_empty_string_list_value_starts_list(self) -> None:
        text = "spinner_frames:\n  - A\n  - B\n"
        d = _parse_skin_yaml(text)
        self.assertEqual(d["spinner_frames"], ["A", "B"])


class LoadSkinYamlTests(unittest.TestCase):
    def test_loads_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.skin.yaml"
            path.write_text(_GTL_YAML, encoding="utf-8")
            cfg = load_skin_yaml(path)
            self.assertEqual(cfg.name, "client_gtl")
            self.assertEqual(cfg.primary_color, "#1A3A6B")
            self.assertEqual(cfg.branding_name, "SEAL · GTL")

    def test_spinner_frames_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.skin.yaml"
            path.write_text(_GTL_YAML, encoding="utf-8")
            cfg = load_skin_yaml(path)
            self.assertIsInstance(cfg.spinner_frames, list)
            self.assertGreater(len(cfg.spinner_frames), 0)

    def test_raises_on_missing_file(self) -> None:
        with self.assertRaises(ValueError):
            load_skin_yaml(Path("/nonexistent/path/test.skin.yaml"))

    def test_minimal_gets_default_spinner(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "min.skin.yaml"
            path.write_text(_MINIMAL_YAML, encoding="utf-8")
            cfg = load_skin_yaml(path)
            self.assertIsInstance(cfg.spinner_frames, list)
            self.assertGreater(len(cfg.spinner_frames), 0)


class SkinToYamlRoundTripTests(unittest.TestCase):
    def test_round_trip_client_gtl(self) -> None:
        cfg = BUILT_IN_SKINS["client_gtl"]
        yaml_str = skin_to_yaml(cfg)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "rt.skin.yaml"
            path.write_text(yaml_str)
            cfg2 = load_skin_yaml(path)
        self.assertEqual(cfg2.name, cfg.name)
        self.assertEqual(cfg2.primary_color, cfg.primary_color)
        self.assertEqual(cfg2.branding_name, cfg.branding_name)
        self.assertEqual(cfg2.branding_tagline, cfg.branding_tagline)
        self.assertEqual(cfg2.spinner_frames, cfg.spinner_frames)

    def test_all_built_ins_round_trip(self) -> None:
        for name, cfg in BUILT_IN_SKINS.items():
            with self.subTest(skin=name):
                yaml_str = skin_to_yaml(cfg)
                with tempfile.TemporaryDirectory() as d:
                    path = Path(d) / f"{name}.skin.yaml"
                    path.write_text(yaml_str)
                    cfg2 = load_skin_yaml(path)
                self.assertEqual(cfg2.primary_color, cfg.primary_color)
                self.assertEqual(cfg2.error_color, cfg.error_color)


class SkinConfigNewFieldsTests(unittest.TestCase):
    def test_default_has_spinner_frames(self) -> None:
        cfg = SkinConfig()
        self.assertIsInstance(cfg.spinner_frames, list)
        self.assertGreater(len(cfg.spinner_frames), 0)

    def test_default_branding(self) -> None:
        cfg = SkinConfig()
        self.assertEqual(cfg.branding_name, "SEAL")
        self.assertNotEqual(cfg.branding_tagline, "")

    def test_from_dict_custom_spinner(self) -> None:
        cfg = SkinConfig.from_dict({"spinner_frames": ["A", "B", "C"]})
        self.assertEqual(cfg.spinner_frames, ["A", "B", "C"])

    def test_from_dict_empty_spinner_falls_back_to_default(self) -> None:
        cfg = SkinConfig.from_dict({"spinner_frames": []})
        self.assertGreater(len(cfg.spinner_frames), 0)

    def test_from_dict_theme_alias(self) -> None:
        cfg = SkinConfig.from_dict({"theme": "gtl", "primary_color": "#1A3A6B"})
        self.assertEqual(cfg.theme, "gtl")

    def test_default_error_and_success_colors(self) -> None:
        cfg = SkinConfig()
        self.assertTrue(cfg.error_color.startswith("#"))
        self.assertTrue(cfg.success_color.startswith("#"))


class BuiltInSkinsTests(unittest.TestCase):
    def test_four_skins_present(self) -> None:
        for name in ("default", "dark", "light", "client_gtl"):
            self.assertIn(name, BUILT_IN_SKINS)

    def test_client_gtl_branding(self) -> None:
        cfg = BUILT_IN_SKINS["client_gtl"]
        self.assertIn("GTL", cfg.branding_name)
        self.assertIn("GTL", cfg.branding_tagline)

    def test_all_have_spinner_frames(self) -> None:
        for name, cfg in BUILT_IN_SKINS.items():
            with self.subTest(skin=name):
                self.assertIsInstance(cfg.spinner_frames, list)
                self.assertGreater(len(cfg.spinner_frames), 0)

    def test_backward_compat_aliases_in_themes(self) -> None:
        for name in ("gtl", "hospital", "minimal"):
            self.assertIn(name, THEMES)

    def test_themes_gtl_is_client_gtl(self) -> None:
        self.assertIs(THEMES["gtl"], BUILT_IN_SKINS["client_gtl"])

    def test_dark_has_bg_color(self) -> None:
        self.assertNotEqual(BUILT_IN_SKINS["dark"].bg_color, "")

    def test_light_has_bg_color(self) -> None:
        self.assertNotEqual(BUILT_IN_SKINS["light"].bg_color, "")


class AnsiSkinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg  = BUILT_IN_SKINS["default"]
        self.ansi = AnsiSkin(self.cfg)

    def test_primary_wraps_with_ansi(self) -> None:
        result = self.ansi.primary("hello")
        self.assertIn("hello", result)
        self.assertIn("\033[", result)
        self.assertIn("\033[0m", result)

    def test_no_color_mode_returns_plain(self) -> None:
        ansi = AnsiSkin(self.cfg, no_color=True)
        self.assertEqual(ansi.primary("hello"), "hello")
        self.assertEqual(ansi.error("oops"), "oops")
        self.assertEqual(ansi.success("ok"), "ok")

    def test_error_and_success_differ(self) -> None:
        err = self.ansi.error("bad")
        ok  = self.ansi.success("good")
        self.assertIn("bad", err)
        self.assertIn("good", ok)
        self.assertIn("\033[", err)
        self.assertIn("\033[", ok)

    def test_spin_returns_frame(self) -> None:
        f0 = self.ansi.spin(0)
        f1 = self.ansi.spin(1)
        self.assertIsInstance(f0, str)
        self.assertNotEqual(f0, f1)

    def test_spin_wraps_around(self) -> None:
        frames = self.cfg.spinner_frames
        self.assertEqual(self.ansi.spin(0), self.ansi.spin(len(frames)))

    def test_brand_header_contains_name_and_tagline(self) -> None:
        header = self.ansi.brand_header()
        self.assertIn(self.cfg.branding_name, header)
        self.assertIn(self.cfg.branding_tagline, header)

    def test_client_gtl_brand_header(self) -> None:
        ansi = AnsiSkin(BUILT_IN_SKINS["client_gtl"])
        header = ansi.brand_header()
        self.assertIn("GTL", header)

    def test_secondary_uses_different_code_from_primary(self) -> None:
        ansi = AnsiSkin(BUILT_IN_SKINS["default"])
        pri = ansi.primary("x")
        sec = ansi.secondary("x")
        # Different colors → different ANSI prefix
        self.assertNotEqual(pri, sec)


class ApplyConsoleTests(unittest.TestCase):
    class _Console:
        pass

    def test_apply_injects_all_helpers(self) -> None:
        ansi    = AnsiSkin(BUILT_IN_SKINS["default"])
        console = self._Console()
        ansi.apply(console)
        for attr in ("primary", "secondary", "accent", "error",
                     "success", "spin", "brand_header"):
            self.assertTrue(hasattr(console, attr), f"missing {attr}")

    def test_apply_sets_skin_reference(self) -> None:
        ansi    = AnsiSkin(BUILT_IN_SKINS["light"])
        console = self._Console()
        ansi.apply(console)
        self.assertIs(console.skin, ansi)

    def test_primary_callable_after_apply(self) -> None:
        ansi    = AnsiSkin(BUILT_IN_SKINS["default"])
        console = self._Console()
        ansi.apply(console)
        result = console.primary("test")
        self.assertIn("test", result)

    def test_no_color_console_returns_plain(self) -> None:
        ansi    = AnsiSkin(BUILT_IN_SKINS["default"], no_color=True)
        console = self._Console()
        ansi.apply(console)
        self.assertEqual(console.primary("raw"), "raw")

    def test_two_consoles_are_independent(self) -> None:
        c1 = self._Console()
        c2 = self._Console()
        AnsiSkin(BUILT_IN_SKINS["default"]).apply(c1)
        AnsiSkin(BUILT_IN_SKINS["dark"]).apply(c2)
        self.assertIsNot(c1.skin, c2.skin)

    def test_spin_callable_after_apply(self) -> None:
        ansi    = AnsiSkin(BUILT_IN_SKINS["default"])
        console = self._Console()
        ansi.apply(console)
        self.assertIsInstance(console.spin(0), str)


class AnsiFgTests(unittest.TestCase):
    def test_returns_ansi_escape(self) -> None:
        esc = _ansi_fg("#FF0000")
        self.assertTrue(esc.startswith("\033[38;2;"))
        self.assertIn("255;0;0", esc)

    def test_invalid_hex_returns_empty(self) -> None:
        self.assertEqual(_ansi_fg("notacolor"), "")

    def test_pure_green(self) -> None:
        esc = _ansi_fg("#00FF00")
        self.assertIn("0;255;0", esc)


class LoadAnsiSkinTests(unittest.TestCase):
    def test_theme_kwarg(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ansi = load_ansi_skin("any", theme="client_gtl",
                                  seal_home=Path(d) / ".seal")
            self.assertIsInstance(ansi, AnsiSkin)
            self.assertIn("GTL", ansi.config.branding_name)

    def test_returns_ansi_skin_instance(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ansi = load_ansi_skin("ghost", seal_home=Path(d) / ".seal")
            self.assertIsInstance(ansi, AnsiSkin)

    def test_no_color_propagated(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ansi = load_ansi_skin("ghost", seal_home=Path(d) / ".seal",
                                  no_color=True)
            self.assertTrue(ansi.no_color)


if __name__ == "__main__":
    unittest.main()
