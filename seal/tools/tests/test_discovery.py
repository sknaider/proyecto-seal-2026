"""Contract tests for seal/tools/discovery.py

Run:
    python3 -m pytest seal/tools/tests/test_discovery.py -v

Or standalone:
    python3 -m unittest seal.tools.tests.test_discovery -v
"""
from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from seal.tools.discovery import (
    DiscoveredModule,
    _extract_tool_names,
    auto_discover,
    import_discovered,
    scan_directory,
)


def _write(directory: Path, filename: str, source: str) -> Path:
    path = directory / filename
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


class ExtractToolNamesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_at_tool_decorator(self) -> None:
        p = _write(self.tmp, "a.py", """
            @tool
            def search(query: str): ...
        """)
        self.assertEqual(_extract_tool_names(p), ["search"])

    def test_at_tool_call_decorator(self) -> None:
        p = _write(self.tmp, "b.py", """
            @tool("search_web")
            def search(query: str): ...
        """)
        self.assertEqual(_extract_tool_names(p), ["search"])

    def test_at_registry_dot_tool(self) -> None:
        p = _write(self.tmp, "c.py", """
            @registry.tool
            def fetch(url: str): ...
        """)
        self.assertEqual(_extract_tool_names(p), ["fetch"])

    def test_register_call_keyword_name(self) -> None:
        p = _write(self.tmp, "d.py", """
            register(name="calculator", fn=calc)
        """)
        self.assertEqual(_extract_tool_names(p), ["calculator"])

    def test_register_call_positional_name(self) -> None:
        p = _write(self.tmp, "e.py", """
            register("calculator", fn=calc)
        """)
        self.assertEqual(_extract_tool_names(p), ["calculator"])

    def test_no_tools_returns_empty(self) -> None:
        p = _write(self.tmp, "f.py", """
            def helper(): pass
            x = 1
        """)
        self.assertEqual(_extract_tool_names(p), [])

    def test_syntax_error_returns_empty(self) -> None:
        p = _write(self.tmp, "bad.py", "def broken(: pass")
        self.assertEqual(_extract_tool_names(p), [])

    def test_multiple_tools_in_one_file(self) -> None:
        p = _write(self.tmp, "multi.py", """
            @tool
            def alpha(): ...

            @tool
            def beta(): ...
        """)
        self.assertCountEqual(_extract_tool_names(p), ["alpha", "beta"])

    def test_async_function_detected(self) -> None:
        p = _write(self.tmp, "async_t.py", """
            @tool
            async def fetch_async(url: str): ...
        """)
        self.assertEqual(_extract_tool_names(p), ["fetch_async"])


class ScanDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_finds_tool_files(self) -> None:
        _write(self.tmp, "tool_a.py", "@tool\ndef alpha(): ...")
        _write(self.tmp, "no_tools.py", "x = 1")
        result = scan_directory(self.tmp)
        self.assertEqual(len(result), 1)
        self.assertIn("alpha", result[0].tool_names)

    def test_skips_underscore_files(self) -> None:
        _write(self.tmp, "__init__.py", "@tool\ndef hidden(): ...")
        _write(self.tmp, "_private.py", "@tool\ndef priv(): ...")
        result = scan_directory(self.tmp)
        self.assertEqual(result, [])

    def test_recursive_finds_nested(self) -> None:
        sub = self.tmp / "sub"
        sub.mkdir()
        _write(sub, "nested.py", "@tool\ndef deep(): ...")
        result = scan_directory(self.tmp, recursive=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].tool_names, ("deep",))

    def test_non_recursive_skips_nested(self) -> None:
        sub = self.tmp / "sub"
        sub.mkdir()
        _write(sub, "nested.py", "@tool\ndef deep(): ...")
        result = scan_directory(self.tmp, recursive=False)
        self.assertEqual(result, [])

    def test_returns_discovered_module_instances(self) -> None:
        _write(self.tmp, "tool_x.py", "@tool\ndef x(): ...")
        result = scan_directory(self.tmp)
        self.assertIsInstance(result[0], DiscoveredModule)


class ImportDiscoveredTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        # clean up any modules we loaded
        for key in list(sys.modules.keys()):
            if key.startswith("_seal_test_"):
                sys.modules.pop(key, None)

    def test_import_executes_module(self) -> None:
        # No @tool here — the scan already extracted names; import just runs the module
        path = _write(self.tmp, "exec_me.py", textwrap.dedent("""
            executed = True
        """))
        dm = DiscoveredModule(path=path, tool_names=("do_thing",))
        result = import_discovered([dm], base_package="_seal_test_")
        self.assertIn(path, result)
        self.assertTrue(result[path].executed)

    def test_import_failure_excluded_from_result(self) -> None:
        path = _write(self.tmp, "broken.py", "raise RuntimeError('boom')")
        dm = DiscoveredModule(path=path, tool_names=())
        result = import_discovered([dm])
        self.assertNotIn(path, result)

    def test_auto_discover_combines_scan_and_import(self) -> None:
        # tool = identity so @tool works without the real registry imported
        _write(self.tmp, "combo.py", textwrap.dedent("""
            tool = lambda f: f

            registered = []

            @tool
            def my_tool(): ...

            registered.append("my_tool")
        """))
        result = auto_discover(self.tmp, base_package="_seal_test_combo_")
        self.assertEqual(len(result), 1)
        mod = next(iter(result.values()))
        self.assertEqual(mod.registered, ["my_tool"])


if __name__ == "__main__":
    unittest.main()
