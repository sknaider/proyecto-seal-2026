# ADA dadito-laptop Tailscale setup

Target:
- Host: `dadito-laptop`
- Tailscale IP: `100.71.150.86`
- Canonical Spark/SOUL IP: `100.75.201.110`

What this package provides:
- Primary Windows Codex App launcher/shortcut tooling: `Start-ADA-Codex-App.ps1`
- Fallback Windows visible Codex terminal launcher: `Start-ADA-Codex-Visible.cmd`
- Windows Claude Code launcher: `Start-ADA-Claude-Code.cmd`
- ADA boot prompt for Codex: `ADA_CODEX_BOOT_PROMPT.txt`
- Claude Code workspace instructions: `CLAUDE.md`

Runtime contract:
- dadito-laptop is the visible client.
- Spark remains canonical for WebChat and SOUL MCP.
- WSL/tmux parity is blocked until WSL2 virtualization can start on the laptop.

Codex App is the preferred flow for William on dadito-laptop. The terminal launcher exists only as fallback/recovery.

Validation commands:

```powershell
powershell -ExecutionPolicy Bypass -File .\agents\ADA\windows\Start-ADA-Codex-App.ps1 -SelfTest
powershell -ExecutionPolicy Bypass -File .\agents\ADA\windows\Start-ADA-Codex-Visible.ps1 -SelfTest
powershell -ExecutionPolicy Bypass -File .\agents\ADA\windows\Start-ADA-Claude-Code.ps1 -SelfTest
```

Expected evidence:
- `codex-cli 0.136.0` or newer.
- `Claude Code 2.1.143` or newer.
- WebChat health returns `seal-chat`.
- MCP health returns `seal-memory-mcp` with `postgresql_pgvector`.
