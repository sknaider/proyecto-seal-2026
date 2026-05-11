"""mcp_server_v3.py — compatibility shim for legacy test suite.

All implementation lives in mcp_server_v4.py. Tests that import mcp_server_v3
work transparently via this shim.
"""
from mcp_server_v4 import *  # noqa: F401, F403
from mcp_server_v4 import (  # noqa: F401
    _rate_check,
    _rate_windows,
    _RATE_LIMIT_DEFAULT,
    _RATE_LIMITS_OVERRIDE,
    _async_cleanup,
    _signal_handler,
    _sync_cleanup,
    _cold_archive_migrate,
    _qdrant,
    _qdrant_lite,
)
