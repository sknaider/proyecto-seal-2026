"""SEAL Skin engine — per-profile color themes for the terminal UI.

Loads brand colors from a profile's config.toml and maps them to curses
color constants.  Ships three built-in themes and supports custom colors
via hex codes (#RRGGBB).

Usage:
    from seal.skin import load_skin, THEMES

    skin = load_skin("acme_corp")            # reads config.toml
    skin = load_skin("acme_corp", theme="gtl")  # force preset
    curses_color = skin.primary              # curses.COLOR_* int

CLI:
    python3 -m seal.skin set --profile acme_corp --theme gtl
    python3 -m seal.skin set --profile acme_corp --primary '#1A3A6B'
    python3 -m seal.skin show --profile acme_corp
"""
from __future__ import annotations

import argparse
import curses
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Curses color palette (RGB approximations for nearest-color mapping)
# ---------------------------------------------------------------------------

_CURSES_PALETTE: list[tuple[int, int, int, int]] = [
    # (curses_constant, r, g, b)
    (curses.COLOR_BLACK,   0,   0,   0),
    (curses.COLOR_RED,     170, 0,   0),
    (curses.COLOR_GREEN,   0,   170, 0),
    (curses.COLOR_YELLOW,  170, 170, 0),
    (curses.COLOR_BLUE,    0,   0,   170),
    (curses.COLOR_MAGENTA, 170, 0,   170),
    (curses.COLOR_CYAN,    0,   170, 170),
    (curses.COLOR_WHITE,   170, 170, 170),
]


# ---------------------------------------------------------------------------
# Predefined themes
# ---------------------------------------------------------------------------

@dataclass
class SkinConfig:
    """Brand colors as hex strings (#RRGGBB)."""
    primary_color:   str = "#4A7FB5"   # main UI chrome
    secondary_color: str = "#AAAAAA"   # muted / labels
    accent_color:    str = "#E8A838"   # highlights / alerts
    theme:           str = "default"


THEMES: dict[str, SkinConfig] = {
    "gtl": SkinConfig(
        primary_color   = "#1A3A6B",
        secondary_color = "#4A7FB5",
        accent_color    = "#E8A838",
        theme           = "gtl",
    ),
    "hospital": SkinConfig(
        primary_color   = "#2E6DA4",
        secondary_color = "#5BA3C9",
        accent_color    = "#4CAF50",
        theme           = "hospital",
    ),
    "minimal": SkinConfig(
        primary_color   = "#FFFFFF",
        secondary_color = "#AAAAAA",
        accent_color    = "#00FF00",
        theme           = "minimal",
    ),
}


# ---------------------------------------------------------------------------
# Skin — runtime object (config → curses ints)
# ---------------------------------------------------------------------------

@dataclass
class Skin:
    """Resolved skin: hex colors mapped to curses color constants."""
    primary:   int = curses.COLOR_BLUE
    secondary: int = curses.COLOR_WHITE
    accent:    int = curses.COLOR_YELLOW
    config:    Optional[SkinConfig] = field(default=None, repr=False)

    @classmethod
    def from_config(cls, cfg: SkinConfig) -> "Skin":
        return cls(
            primary   = hex_to_curses(cfg.primary_color),
            secondary = hex_to_curses(cfg.secondary_color),
            accent    = hex_to_curses(cfg.accent_color),
            config    = cfg,
        )

    def init_pairs(self, base_pair: int = 20) -> None:
        """Register curses color pairs for primary/secondary/accent.

        Pair base_pair+0 = primary on default bg
        Pair base_pair+1 = secondary on default bg
        Pair base_pair+2 = accent on default bg
        """
        curses.init_pair(base_pair,     self.primary,   -1)
        curses.init_pair(base_pair + 1, self.secondary, -1)
        curses.init_pair(base_pair + 2, self.accent,    -1)

    def pair(self, variant: str = "primary", base_pair: int = 20) -> int:
        """Return curses.color_pair for primary/secondary/accent (safe fallback)."""
        offset = {"primary": 0, "secondary": 1, "accent": 2}.get(variant, 0)
        try:
            return curses.color_pair(base_pair + offset)
        except curses.error:
            return 0


DEFAULT_SKIN = Skin()


# ---------------------------------------------------------------------------
# Hex conversion helpers
# ---------------------------------------------------------------------------


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Parse #RRGGBB (or #RGB) and return (r, g, b) in 0-255."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        raise ValueError(f"Invalid hex color: {hex_color!r}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def hex_to_curses(hex_color: str) -> int:
    """Return the nearest curses.COLOR_* constant for a hex color string."""
    try:
        r, g, b = _hex_to_rgb(hex_color)
    except ValueError:
        return curses.COLOR_WHITE
    best_const = curses.COLOR_WHITE
    best_dist  = float("inf")
    for const, cr, cg, cb in _CURSES_PALETTE:
        dist = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
        if dist < best_dist:
            best_dist  = dist
            best_const = const
    return best_const


def _is_dark_terminal() -> bool:
    """Heuristic: COLORFGBG bg value < 8 → dark background."""
    colorfgbg = os.environ.get("COLORFGBG", "15;0")
    parts = colorfgbg.split(";")
    try:
        bg = int(parts[-1])
        return bg < 8
    except (ValueError, IndexError):
        return True  # assume dark by default


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------


def _seal_home(override: Optional[Path] = None) -> Path:
    return override or Path(os.environ.get("SEAL_HOME", Path.home() / ".seal"))


def _config_path(profile: str, seal_home: Optional[Path] = None) -> Path:
    return _seal_home(seal_home) / "profiles" / profile / "config.toml"


def _parse_toml_minimal(text: str) -> dict:
    """Parse flat TOML sections into nested dicts.

    Handles only [section], key = "value", and key = 'value'.
    Sufficient for reading the [skin] block from config.toml.
    """
    result: dict[str, dict] = {}
    section = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]") and not line.startswith("[["):
            section = line[1:-1].strip()
            result.setdefault(section, {})
        elif "=" in line and section:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            # Strip surrounding quotes
            if (val.startswith('"') and val.endswith('"')) or \
               (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            result[section][key] = val
    return result


def _read_config(profile: str, seal_home: Optional[Path] = None) -> dict:
    path = _config_path(profile, seal_home)
    if not path.exists():
        return {}
    try:
        import tomllib  # Python 3.11+
        with path.open("rb") as f:
            return tomllib.load(f)
    except ImportError:
        return _parse_toml_minimal(path.read_text())


def _read_skin_config(
    profile: str, seal_home: Optional[Path] = None
) -> Optional[SkinConfig]:
    """Return SkinConfig from profile's config.toml, or None if not set."""
    data = _read_config(profile, seal_home)
    skin_data = data.get("skin", {})
    if not skin_data:
        return None
    return SkinConfig(
        primary_color   = skin_data.get("primary_color",   "#4A7FB5"),
        secondary_color = skin_data.get("secondary_color", "#AAAAAA"),
        accent_color    = skin_data.get("accent_color",    "#E8A838"),
        theme           = skin_data.get("theme",           "default"),
    )


def _write_skin_config(
    profile: str,
    config: SkinConfig,
    seal_home: Optional[Path] = None,
) -> None:
    """Write or replace the [skin] section in config.toml."""
    path = _config_path(profile, seal_home)
    if path.exists():
        original = path.read_text()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        original = ""

    # Remove existing [skin] block
    lines = original.splitlines(keepends=True)
    cleaned: list[str] = []
    in_skin = False
    for line in lines:
        stripped = line.strip()
        if stripped == "[skin]":
            in_skin = True
            continue
        if in_skin and stripped.startswith("[") and not stripped.startswith("[skin]"):
            in_skin = False
        if not in_skin:
            cleaned.append(line)

    skin_block = (
        "\n[skin]\n"
        f'primary_color   = "{config.primary_color}"\n'
        f'secondary_color = "{config.secondary_color}"\n'
        f'accent_color    = "{config.accent_color}"\n'
        f'theme           = "{config.theme}"\n'
    )
    new_text = "".join(cleaned).rstrip("\n") + skin_block
    path.write_text(new_text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_skin(
    profile: str,
    theme: Optional[str] = None,
    seal_home: Optional[Path] = None,
    adapt_to_terminal: bool = True,
) -> Skin:
    """Return a Skin for *profile*.

    Resolution order:
      1. *theme* kwarg (if given, use THEMES preset)
      2. Saved theme name in config.toml  (if it matches a preset)
      3. Custom hex colors in config.toml
      4. THEMES["minimal"] on dark terminal, else default Skin

    adapt_to_terminal: if True and no config found, pick minimal (dark) or
    default (light/unknown) based on COLORFGBG.
    """
    if theme and theme in THEMES:
        return Skin.from_config(THEMES[theme])

    cfg = _read_skin_config(profile, seal_home)
    if cfg is not None:
        if cfg.theme in THEMES and cfg.theme != "default":
            return Skin.from_config(THEMES[cfg.theme])
        return Skin.from_config(cfg)

    if adapt_to_terminal:
        fallback = THEMES["minimal"] if _is_dark_terminal() else SkinConfig()
        return Skin.from_config(fallback)

    return DEFAULT_SKIN


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="seal skin",
        description="Manage per-profile color themes.",
    )
    sub = p.add_subparsers(dest="command")

    set_p = sub.add_parser("set", help="Set skin for a profile")
    set_p.add_argument("--profile", required=True)
    set_p.add_argument("--theme", default=None,
                       help=f"Preset theme ({', '.join(THEMES)})")
    set_p.add_argument("--primary",   default=None, metavar="HEX")
    set_p.add_argument("--secondary", default=None, metavar="HEX")
    set_p.add_argument("--accent",    default=None, metavar="HEX")
    set_p.add_argument("--seal-home", type=Path, default=None)

    show_p = sub.add_parser("show", help="Show current skin for a profile")
    show_p.add_argument("--profile", required=True)
    show_p.add_argument("--seal-home", type=Path, default=None)

    list_p = sub.add_parser("list", help="List available themes")

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.command == "set":
        if args.theme:
            if args.theme not in THEMES:
                print(f"  [FAIL] unknown theme '{args.theme}'. "
                      f"Available: {', '.join(THEMES)}", file=sys.stderr)
                return 1
            cfg = THEMES[args.theme]
        else:
            existing = _read_skin_config(args.profile, args.seal_home) or SkinConfig()
            cfg = SkinConfig(
                primary_color   = args.primary   or existing.primary_color,
                secondary_color = args.secondary or existing.secondary_color,
                accent_color    = args.accent    or existing.accent_color,
                theme           = "custom",
            )
        try:
            _write_skin_config(args.profile, cfg, args.seal_home)
            skin = Skin.from_config(cfg)
            print(f"  [OK]  skin updated for '{args.profile}'")
            print(f"        primary={cfg.primary_color} → curses {skin.primary}")
            print(f"        secondary={cfg.secondary_color} → curses {skin.secondary}")
            print(f"        accent={cfg.accent_color} → curses {skin.accent}")
        except Exception as exc:
            print(f"  [FAIL] {exc}", file=sys.stderr)
            return 1
        return 0

    if args.command == "show":
        cfg = _read_skin_config(args.profile, args.seal_home)
        if cfg is None:
            print(f"  [SKIP] no skin config for '{args.profile}' — using defaults")
            return 0
        skin = Skin.from_config(cfg)
        print()
        print(f"  Profile:    {args.profile}")
        print(f"  Theme:      {cfg.theme}")
        print(f"  Primary:    {cfg.primary_color} → curses {skin.primary}")
        print(f"  Secondary:  {cfg.secondary_color} → curses {skin.secondary}")
        print(f"  Accent:     {cfg.accent_color} → curses {skin.accent}")
        print()
        return 0

    if args.command == "list":
        print()
        for name, cfg in THEMES.items():
            print(f"  {name:<12} primary={cfg.primary_color}  "
                  f"secondary={cfg.secondary_color}  accent={cfg.accent_color}")
        print()
        return 0

    _parse_args(["--help"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
