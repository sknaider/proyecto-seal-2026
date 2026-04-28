"""Tool auto-discovery via AST scan.

Scans directories for Python files that declare tools and returns
metadata without importing any code.  Actual registration happens
when import_discovered() is called.

Detection heuristics (AST-only, zero execution):
  * Function decorated with @tool, @tool("name"), @registry.tool, etc.
  * Module-level call to register(name=...) or register("name", ...)

Neither heuristic requires a specific import alias — any decoration or
call matching the shape is counted.

Usage:
    discovered = scan_directory(Path("seal/tools/contrib"))
    # inspect without importing:
    for dm in discovered:
        print(dm.path, dm.tool_names)
    # import + register:
    import_discovered(discovered)
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType


@dataclass(frozen=True)
class DiscoveredModule:
    """One tool-bearing Python file found by the scanner."""

    path: Path
    tool_names: tuple[str, ...] = field(default_factory=tuple)


def scan_directory(
    directory: Path,
    *,
    recursive: bool = True,
) -> list[DiscoveredModule]:
    """Return DiscoveredModule entries for every tool-bearing .py in *directory*.

    Files whose name starts with ``_`` are skipped (private / dunder).
    Uses AST-only analysis — no imports, no execution.
    """
    glob = "**/*.py" if recursive else "*.py"
    found: list[DiscoveredModule] = []
    for path in sorted(directory.glob(glob)):
        if path.name.startswith("_"):
            continue
        names = _extract_tool_names(path)
        if names:
            found.append(DiscoveredModule(path=path, tool_names=tuple(names)))
    return found


def import_discovered(
    modules: list[DiscoveredModule],
    *,
    base_package: str | None = None,
) -> dict[Path, ModuleType]:
    """Import each discovered module and return a path → module mapping.

    Importing a tool module triggers whatever top-level registration the
    module performs (decorator calls, register() calls, etc.).
    """
    result: dict[Path, ModuleType] = {}
    for dm in modules:
        mod = _import_path(dm.path, base_package=base_package)
        if mod is not None:
            result[dm.path] = mod
    return result


def auto_discover(
    directory: Path,
    *,
    recursive: bool = True,
    base_package: str | None = None,
) -> dict[Path, ModuleType]:
    """Scan and import in a single call — convenience wrapper."""
    discovered = scan_directory(directory, recursive=recursive)
    return import_discovered(discovered, base_package=base_package)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _extract_tool_names(path: Path) -> list[str]:
    """AST-scan *path* and return declared tool names (empty list if none)."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, OSError):
        return []

    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _has_tool_decorator(node):
                names.append(node.name)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            name = _register_call_name(node.value)
            if name is not None:
                names.append(name)
    return names


def _has_tool_decorator(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Return True if the function has a @tool or @*.tool decorator."""
    for dec in node.decorator_list:
        # @tool
        if isinstance(dec, ast.Name) and dec.id == "tool":
            return True
        # @tool("name") or @tool(...)
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Name)
            and dec.func.id == "tool"
        ):
            return True
        # @registry.tool
        if isinstance(dec, ast.Attribute) and dec.attr == "tool":
            return True
        # @registry.tool(...)
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr == "tool"
        ):
            return True
    return False


def _register_call_name(call: ast.Call) -> str | None:
    """Return tool name if *call* is register(name=...) or register("name", ...)."""
    func = call.func
    func_name = (
        func.id
        if isinstance(func, ast.Name)
        else (func.attr if isinstance(func, ast.Attribute) else None)
    )
    if func_name != "register":
        return None
    # keyword: name="my_tool"
    for kw in call.keywords:
        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    # positional first arg: register("my_tool", ...)
    if call.args and isinstance(call.args[0], ast.Constant):
        return str(call.args[0].value)
    return None


# ---------------------------------------------------------------------------
# Import helper
# ---------------------------------------------------------------------------


def _import_path(
    path: Path,
    *,
    base_package: str | None,
) -> ModuleType | None:
    """Import a .py file by absolute path; return None on any failure."""
    module_name = path.stem
    if base_package:
        module_name = f"{base_package}.{module_name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        sys.modules.pop(module_name, None)
        return None
    return mod
