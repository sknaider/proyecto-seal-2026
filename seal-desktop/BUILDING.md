# SEAL App Desktop Builds

SEAL App supports Linux and Windows from the same source tree.

## Linux

```bash
./build-linux.sh
```

Outputs:
- `dist/deb/seal-companion_*.deb`
- `dist/deb/seal-team-dashboard_*.deb`
- Tauri bundles under `src-tauri/target/release/bundle/` (`.deb`, `.rpm`, `.AppImage`)

## Windows

Run on a Windows machine or a Windows CI runner with Node, Rust, and WebView2:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-windows.ps1 -Arch x64
```

Outputs:
- `seal-app.exe`
- Tauri installer bundles under `src-tauri\target\x86_64-pc-windows-msvc\release\bundle\`

Runtime launch:

```powershell
powershell -ExecutionPolicy Bypass -File .\launch-seal-app.ps1
```

The launcher starts `companion_core` on `127.0.0.1:8769` and then opens the
native `seal-app.exe` when present. If no exe is present, it opens Edge in app
mode.

## User Data Paths

The backend uses OS-native writable directories:

- Linux: `$XDG_CONFIG_HOME/seal-app` and `$XDG_DATA_HOME/seal-app`
- Windows: `%APPDATA%\seal-app` and `%LOCALAPPDATA%\seal-app`
- macOS: `~/Library/Application Support/seal-app`

Environment overrides remain supported:
`SEAL_DB_PATH`, `SEAL_TOML_PATH`, `SEAL_CONFIG_DIR`, `SEAL_DATA_DIR`,
`SOUL_VAULT_DIR`, `SEAL_UI_DIR`, `SEAL_VOICE_MODELS_DIR`.
