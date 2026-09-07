"""seal/skin.py — YAML-based per-profile skin/theme engine.

Provides terminal styling via ANSI escape codes (24-bit color)
and curses color constants.  Ships four built-in skins and supports
custom skin YAML files.

YAML skin file format:
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
      ...

Usage:
    from seal.skin import load_skin_yaml, AnsiSkin, BUILT_IN_SKINS

    cfg   = load_skin_yaml(Path("acme.skin.yaml"))
    ansi  = AnsiSkin(cfg)
    ansi.apply(console)           # inject helpers into any object
    styled = ansi.primary("ok")  # → ANSI-wrapped string

    # Curses (unchanged):
    from seal.skin import load_skin
    skin = load_skin("acme_corp")
    skin.init_pairs()
"""
from __future__ import annotations

import argparse
import curses
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ── ANSI helpers ──────────────────────────────────────────────────────────────

_RESET = "\033[0m"


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Parse #RRGGBB or #RGB → (r, g, b) 0-255."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        raise ValueError(f"invalid hex color: {hex_color!r}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _ansi_fg(hex_color: str) -> str:
    """ANSI 24-bit foreground escape for a hex color, '' on error."""
    try:
        r, g, b = _hex_to_rgb(hex_color)
        return f"\033[38;2;{r};{g};{b}m"
    except ValueError:
        return ""


def _ansi_bg(hex_color: str) -> str:
    """ANSI 24-bit background escape for a hex color, '' if empty/invalid."""
    if not hex_color:
        return ""
    try:
        r, g, b = _hex_to_rgb(hex_color)
        return f"\033[48;2;{r};{g};{b}m"
    except ValueError:
        return ""


def _styled(text: str, fg: str, bg: str = "") -> str:
    if not fg and not bg:
        return text
    return f"{bg}{fg}{text}{_RESET}"


# ── Curses palette ────────────────────────────────────────────────────────────

_CURSES_PALETTE: list[tuple[int, int, int, int]] = [
    (curses.COLOR_BLACK,   0,   0,   0),
    (curses.COLOR_RED,     170, 0,   0),
    (curses.COLOR_GREEN,   0,   170, 0),
    (curses.COLOR_YELLOW,  170, 170, 0),
    (curses.COLOR_BLUE,    0,   0,   170),
    (curses.COLOR_MAGENTA, 170, 0,   170),
    (curses.COLOR_CYAN,    0,   170, 170),
    (curses.COLOR_WHITE,   170, 170, 170),
]


def hex_to_curses(hex_color: str) -> int:
    """Nearest curses.COLOR_* constant for a hex color string."""
    try:
        r, g, b = _hex_to_rgb(hex_color)
    except ValueError:
        return curses.COLOR_WHITE
    best, best_dist = curses.COLOR_WHITE, float("inf")
    for const, cr, cg, cb in _CURSES_PALETTE:
        d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
        if d < best_dist:
            best_dist, best = d, const
    return best


# ── SkinConfig ────────────────────────────────────────────────────────────────

_DEFAULT_SPINNER: list[str] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


@dataclass
class SkinConfig:
    """Full skin specification: colors, branding, and animation frames."""
    name:             str       = "default"
    primary_color:    str       = "#4A7FB5"
    secondary_color:  str       = "#AAAAAA"
    accent_color:     str       = "#E8A838"
    bg_color:         str       = ""           # empty = terminal default
    error_color:      str       = "#CC3333"
    success_color:    str       = "#33AA33"
    spinner_frames:   list[str] = field(default_factory=lambda: list(_DEFAULT_SPINNER))
    branding_name:    str       = "SEAL"
    branding_tagline: str       = "Secure Extensible Agent Layer"
    theme:            str       = "default"    # backward-compat alias for name

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SkinConfig":
        frames = d.get("spinner_frames")
        if not isinstance(frames, list) or not frames:
            frames = list(_DEFAULT_SPINNER)
        name  = str(d.get("name", d.get("theme", "custom")))
        theme = str(d.get("theme", d.get("name", "custom")))
        return cls(
            name             = name,
            primary_color    = str(d.get("primary_color",    "#4A7FB5")),
            secondary_color  = str(d.get("secondary_color",  "#AAAAAA")),
            accent_color     = str(d.get("accent_color",     "#E8A838")),
            bg_color         = str(d.get("bg_color",         "")),
            error_color      = str(d.get("error_color",      "#CC3333")),
            success_color    = str(d.get("success_color",    "#33AA33")),
            spinner_frames   = [str(f) for f in frames],
            branding_name    = str(d.get("branding_name",    "SEAL")),
            branding_tagline = str(d.get("branding_tagline", "Secure Extensible Agent Layer")),
            theme            = theme,
        )


# ── Built-in skins ────────────────────────────────────────────────────────────

BUILT_IN_SKINS: dict[str, SkinConfig] = {
    "default": SkinConfig(
        name             = "default",
        primary_color    = "#4A7FB5",
        secondary_color  = "#AAAAAA",
        accent_color     = "#E8A838",
        branding_name    = "SEAL",
        branding_tagline = "Secure Extensible Agent Layer",
        theme            = "default",
    ),
    "dark": SkinConfig(
        name             = "dark",
        primary_color    = "#5DADE2",
        secondary_color  = "#AAAAAA",
        accent_color     = "#F39C12",
        bg_color         = "#1A1A2E",
        error_color      = "#E74C3C",
        success_color    = "#2ECC71",
        branding_name    = "SEAL",
        branding_tagline = "Secure Extensible Agent Layer",
        theme            = "dark",
    ),
    "light": SkinConfig(
        name             = "light",
        primary_color    = "#2874A6",
        secondary_color  = "#666666",
        accent_color     = "#B7950B",
        bg_color         = "#F8F9FA",
        error_color      = "#C0392B",
        success_color    = "#1E8449",
        branding_name    = "SEAL",
        branding_tagline = "Secure Extensible Agent Layer",
        theme            = "light",
    ),
    "client_gtl": SkinConfig(
        name             = "client_gtl",
        primary_color    = "#1A3A6B",
        secondary_color  = "#4A7FB5",
        accent_color     = "#E8A838",
        error_color      = "#CC3333",
        success_color    = "#33AA33",
        branding_name    = "SEAL · GTL",
        branding_tagline = "GTL Consulting Intelligence",
        theme            = "client_gtl",
    ),
}

# THEMES includes built-ins plus legacy aliases (backward compat)
THEMES: dict[str, SkinConfig] = {
    **BUILT_IN_SKINS,
    "gtl": BUILT_IN_SKINS["client_gtl"],
    "hospital": SkinConfig(
        name             = "hospital",
        primary_color    = "#2E6DA4",
        secondary_color  = "#5BA3C9",
        accent_color     = "#4CAF50",
        branding_name    = "SEAL",
        branding_tagline = "Secure Extensible Agent Layer",
        theme            = "hospital",
    ),
    "minimal": SkinConfig(
        name             = "minimal",
        primary_color    = "#FFFFFF",
        secondary_color  = "#AAAAAA",
        accent_color     = "#00FF00",
        branding_name    = "SEAL",
        branding_tagline = "Secure Extensible Agent Layer",
        theme            = "minimal",
    ),
}


# ── Curses Skin (unchanged API) ───────────────────────────────────────────────

@dataclass
class Skin:
    """Resolved skin: hex colors mapped to curses color constants."""
    primary:   int                  = curses.COLOR_BLUE
    secondary: int                  = curses.COLOR_WHITE
    accent:    int                  = curses.COLOR_YELLOW
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
        """Register curses color pairs (primary/secondary/accent on default bg)."""
        curses.init_pair(base_pair,     self.primary,   -1)
        curses.init_pair(base_pair + 1, self.secondary, -1)
        curses.init_pair(base_pair + 2, self.accent,    -1)

    def pair(self, variant: str = "primary", base_pair: int = 20) -> int:
        """curses.color_pair for primary/secondary/accent (safe fallback)."""
        offset = {"primary": 0, "secondary": 1, "accent": 2}.get(variant, 0)
        try:
            return curses.color_pair(base_pair + offset)
        except curses.error:
            return 0


DEFAULT_SKIN = Skin()


# ── AnsiSkin ──────────────────────────────────────────────────────────────────

class AnsiSkin:
    """ANSI 24-bit color skin — no curses required.

    Usage:
        ansi = AnsiSkin(cfg)
        print(ansi.primary("hello"))     # → ANSI-wrapped string
        ansi.apply(console)              # inject helpers into any object
    """

    def __init__(self, config: SkinConfig, no_color: bool = False) -> None:
        self.config   = config
        self.no_color = no_color
        self._fg_pri  = _ansi_fg(config.primary_color)
        self._fg_sec  = _ansi_fg(config.secondary_color)
        self._fg_acc  = _ansi_fg(config.accent_color)
        self._fg_err  = _ansi_fg(config.error_color)
        self._fg_ok   = _ansi_fg(config.success_color)
        self._bg      = _ansi_bg(config.bg_color)

    def _wrap(self, text: str, fg: str) -> str:
        if self.no_color:
            return text
        return _styled(text, fg, self._bg)

    def primary(self, text: str) -> str:
        return self._wrap(text, self._fg_pri)

    def secondary(self, text: str) -> str:
        return self._wrap(text, self._fg_sec)

    def accent(self, text: str) -> str:
        return self._wrap(text, self._fg_acc)

    def error(self, text: str) -> str:
        return self._wrap(text, self._fg_err)

    def success(self, text: str) -> str:
        return self._wrap(text, self._fg_ok)

    def spin(self, frame: int) -> str:
        """Spinner frame at index *frame* (wraps around)."""
        frames = self.config.spinner_frames or _DEFAULT_SPINNER
        return frames[frame % len(frames)]

    def brand_header(self) -> str:
        """One-line branded header string."""
        name = self.config.branding_name
        tag  = self.config.branding_tagline
        return self.primary(f"  {name}") + "  " + self.secondary(tag)

    def apply(self, console: Any) -> None:
        """Inject styled helpers into *console* object.

        After apply(console):
            console.primary(text)    → ANSI-wrapped string
            console.secondary(text)  → ANSI-wrapped string
            console.accent(text)     → ANSI-wrapped string
            console.error(text)      → ANSI-wrapped string
            console.success(text)    → ANSI-wrapped string
            console.spin(frame)      → spinner character
            console.brand_header()   → branding line
            console.skin             → this AnsiSkin instance
        """
        console.primary      = self.primary
        console.secondary    = self.secondary
        console.accent       = self.accent
        console.error        = self.error
        console.success      = self.success
        console.spin         = self.spin
        console.brand_header = self.brand_header
        console.skin         = self


# ── YAML parser ───────────────────────────────────────────────────────────────

_KV_RE     = re.compile(r"^([\w\-]+)\s*:\s*(.*)")
_LIST_RE   = re.compile(r"^\s{2,}-\s+(.*)")
_QUOTED_RE = re.compile(r'^"(.*)"$|^\'(.*)\'$')


def _unquote(s: str) -> str:
    m = _QUOTED_RE.match(s.strip())
    if m:
        return m.group(1) if m.group(1) is not None else m.group(2)
    return s.strip()


def _parse_skin_yaml(text: str) -> dict[str, Any]:
    """Parse a skin YAML file into a plain dict.

    Supports scalars, quoted strings, booleans, and flat string lists.
    No multi-level nesting needed for SkinConfig fields.
    """
    result: dict[str, Any] = {}
    cur_key:  Optional[str]       = None
    cur_list: Optional[list[str]] = None

    for raw in text.splitlines():
        line = raw.rstrip("\n")

        if not line.strip() or line.strip().startswith("#"):
            continue

        m = _LIST_RE.match(line)
        if m and cur_list is not None:
            cur_list.append(_unquote(m.group(1)))
            continue

        m = _KV_RE.match(line)
        if m:
            if cur_key is not None and cur_list is not None:
                result[cur_key] = cur_list
                cur_list = None

            key = m.group(1)
            val = m.group(2).strip()

            if val == "":
                cur_key  = key
                cur_list = []
            else:
                cur_key  = key
                cur_list = None
                uv = _unquote(val)
                if uv.lower() == "true":
                    result[key] = True
                elif uv.lower() == "false":
                    result[key] = False
                else:
                    result[key] = uv

    if cur_key is not None and cur_list is not None:
        result[cur_key] = cur_list

    return result


def load_skin_yaml(path: Path) -> SkinConfig:
    """Load a SkinConfig from a YAML skin file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read skin file {path}: {exc}") from exc
    return SkinConfig.from_dict(_parse_skin_yaml(text))


def skin_to_yaml(cfg: SkinConfig) -> str:
    """Serialize a SkinConfig to canonical YAML skin string."""
    lines = [
        f"name: {cfg.name}",
        f'branding_name: "{cfg.branding_name}"',
        f'branding_tagline: "{cfg.branding_tagline}"',
        f'primary_color: "{cfg.primary_color}"',
        f'secondary_color: "{cfg.secondary_color}"',
        f'accent_color: "{cfg.accent_color}"',
    ]
    if cfg.bg_color:
        lines.append(f'bg_color: "{cfg.bg_color}"')
    lines.append(f'error_color: "{cfg.error_color}"')
    lines.append(f'success_color: "{cfg.success_color}"')
    lines.append("spinner_frames:")
    for frame in cfg.spinner_frames:
        lines.append(f'  - "{frame}"')
    return "\n".join(lines) + "\n"


# ── Config I/O (TOML — backward compat) ──────────────────────────────────────

def _seal_home(override: Optional[Path] = None) -> Path:
    return override or Path(os.environ.get("SEAL_HOME", str(Path.home() / ".seal")))


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
        import tomllib
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


# ── Terminal detection ────────────────────────────────────────────────────────

def _is_dark_terminal() -> bool:
    """Heuristic: COLORFGBG bg value < 8 → dark background."""
    colorfgbg = os.environ.get("COLORFGBG", "15;0")
    parts = colorfgbg.split(";")
    try:
        return int(parts[-1]) < 8
    except (ValueError, IndexError):
        return True


# ── Public API ────────────────────────────────────────────────────────────────

def load_skin(
    profile: str,
    theme: Optional[str] = None,
    seal_home: Optional[Path] = None,
    adapt_to_terminal: bool = True,
) -> Skin:
    """Return a curses Skin for *profile*.

    Resolution order:
      1. *theme* kwarg (if given, use THEMES preset)
      2. Saved theme name in config.toml (if it matches a preset)
      3. Custom hex colors in config.toml
      4. dark/default built-in based on terminal background
    """
    if theme and theme in THEMES:
        return Skin.from_config(THEMES[theme])

    cfg = _read_skin_config(profile, seal_home)
    if cfg is not None:
        if cfg.theme in THEMES and cfg.theme != "default":
            return Skin.from_config(THEMES[cfg.theme])
        return Skin.from_config(cfg)

    if adapt_to_terminal:
        fallback = THEMES["minimal"] if _is_dark_terminal() else THEMES["default"]
        return Skin.from_config(fallback)

    return DEFAULT_SKIN


def load_ansi_skin(
    profile: str,
    theme: Optional[str] = None,
    seal_home: Optional[Path] = None,
    adapt_to_terminal: bool = True,
    no_color: bool = False,
) -> "AnsiSkin":
    """Return an AnsiSkin for *profile* (ANSI 24-bit, no curses)."""
    if theme and theme in THEMES:
        return AnsiSkin(THEMES[theme], no_color=no_color)

    cfg = _read_skin_config(profile, seal_home)
    if cfg is not None:
        if cfg.theme in THEMES and cfg.theme != "default":
            return AnsiSkin(THEMES[cfg.theme], no_color=no_color)
        return AnsiSkin(cfg, no_color=no_color)

    if adapt_to_terminal:
        fallback = THEMES["dark"] if _is_dark_terminal() else THEMES["light"]
        return AnsiSkin(fallback, no_color=no_color)

    return AnsiSkin(BUILT_IN_SKINS["default"], no_color=no_color)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="seal skin",
        description="Manage per-profile color themes.",
    )
    sub = p.add_subparsers(dest="command")

    set_p = sub.add_parser("set", help="Set skin for a profile")
    set_p.add_argument("--profile",   required=True)
    set_p.add_argument("--theme",     default=None,
                       help=f"Preset theme ({', '.join(THEMES)})")
    set_p.add_argument("--primary",   default=None, metavar="HEX")
    set_p.add_argument("--secondary", default=None, metavar="HEX")
    set_p.add_argument("--accent",    default=None, metavar="HEX")
    set_p.add_argument("--seal-home", type=Path, default=None)

    show_p = sub.add_parser("show", help="Show current skin for a profile")
    show_p.add_argument("--profile",   required=True)
    show_p.add_argument("--seal-home", type=Path, default=None)

    sub.add_parser("list", help="List available themes")

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.command == "set":
        if args.theme:
            if args.theme not in THEMES:
                print(
                    f"error: unknown theme '{args.theme}'. "
                    f"Available: {', '.join(THEMES)}",
                    file=sys.stderr,
                )
                return 1
            cfg = THEMES[args.theme]
        else:
            existing = _read_skin_config(args.profile, args.seal_home) or SkinConfig()
            cfg = SkinConfig(
                name            = "custom",
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
        for name, cfg in BUILT_IN_SKINS.items():
            print(f"  {name:<16} {cfg.branding_name:<22} primary={cfg.primary_color}")
        print()
        return 0

    _parse_args(["--help"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
