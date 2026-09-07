# ADA Codex App Windows

William's preferred flow on dadito-laptop is Codex App for Windows, not the terminal launcher.

Canonical setup:
- AppID: `OpenAI.Codex_2p2nqsd0c76g0!App`
- Package family: `OpenAI.Codex_2p2nqsd0c76g0`
- Installed app path observed: `C:\Program Files\WindowsApps\OpenAI.Codex_26.519.11010.0_x64__2p2nqsd0c76g0\app\Codex.exe`
- Workspace: `C:\Users\Dadito\IA\proyecto-seal`
- Project trust: `[projects.'c:\users\dadito\ia\proyecto-seal'] trust_level = "trusted"` in `C:\Users\Dadito\.codex\config.toml`

Desktop layout:
- Primary: `ADA Codex App.lnk`
- Fallback only: `ADA-Codex-Terminal-Fallback.cmd`

Validation:

```powershell
powershell -ExecutionPolicy Bypass -File .\agents\ADA\windows\Start-ADA-Codex-App.ps1 -SelfTest
```

Expected evidence:
- Codex app found with AppID above.
- Workspace project trust present in `config.toml`.
- WebChat health returns `seal-chat`.
- MCP health returns `seal-memory-mcp` with `postgresql_pgvector`.
- Desktop shortcut exists.

Usage:
1. Open `ADA Codex App.lnk` from the Windows desktop.
2. In Codex App, open/select `C:\Users\Dadito\IA\proyecto-seal`.
3. ADA identity comes from `AGENTS.md` in the workspace and SOUL/WebChat endpoints over Tailscale.
4. Terminal launcher is only a recovery path.
