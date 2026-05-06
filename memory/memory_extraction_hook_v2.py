"""Compatibility shim — sessions started before the v2→no-suffix rename.
New sessions call memory_extraction_hook.py directly via settings.json.
"""
import os, sys

target = os.path.join(os.path.dirname(__file__), "memory_extraction_hook.py")
os.execv(sys.executable, [sys.executable, target] + sys.argv[1:])
