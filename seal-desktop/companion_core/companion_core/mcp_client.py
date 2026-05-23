"""
Minimal MCP stdio client.
Protocol: JSON-RPC 2.0 over subprocess stdin/stdout (MCP spec 2024-11-05).
"""
import asyncio
import json
import os
from typing import Any


class McpClient:
    """Start MCP server subprocess, list tools, call tools."""

    def __init__(self, command: str, args: list[str], env: dict[str, str] | None = None):
        self.command = command
        self.args = args
        self._env: dict[str, str] = {**os.environ, **(env or {})}
        self._proc: asyncio.subprocess.Process | None = None
        self._req_id = 0

    async def start(self, timeout: float = 10.0) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            self.command, *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=self._env,
        )
        await asyncio.wait_for(self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "companion_core", "version": "0.1.0"},
        }), timeout=timeout)

    async def list_tools(self, timeout: float = 10.0) -> list[dict]:
        result = await asyncio.wait_for(self._rpc("tools/list", {}), timeout=timeout)
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: dict, timeout: float = 30.0) -> Any:
        result = await asyncio.wait_for(
            self._rpc("tools/call", {"name": name, "arguments": arguments}),
            timeout=timeout,
        )
        return result.get("content", result)

    async def close(self) -> None:
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.close()
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass

    async def _rpc(self, method: str, params: dict) -> dict:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise RuntimeError("McpClient not started")
        self._req_id += 1
        msg = json.dumps({"jsonrpc": "2.0", "id": self._req_id, "method": method, "params": params}) + "\n"
        self._proc.stdin.write(msg.encode())
        await self._proc.stdin.drain()
        line = await self._proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP server closed connection")
        response = json.loads(line.decode())
        if "error" in response:
            raise RuntimeError(response["error"].get("message", "MCP error"))
        return response.get("result", {})
