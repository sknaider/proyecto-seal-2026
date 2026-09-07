param(
  [string]$BackendPort = $(if ($env:SEAL_BACKEND_PORT) { $env:SEAL_BACKEND_PORT } else { "8769" })
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:SEAL_BACKEND_PORT = $BackendPort
$Url = "http://127.0.0.1:$BackendPort/"

python "$Root\scripts\start_backend.py"

$ExeCandidates = @(
  "$Root\src-tauri\target\release\seal-app.exe",
  "$Root\src-tauri\target\x86_64-pc-windows-msvc\release\seal-app.exe",
  "$Root\dist\windows\seal-app.exe"
)

foreach ($Exe in $ExeCandidates) {
  if (Test-Path $Exe) {
    Start-Process -FilePath $Exe
    exit 0
  }
}

$Edge = "$env:ProgramFiles (x86)\Microsoft\Edge\Application\msedge.exe"
if (Test-Path $Edge) {
  Start-Process -FilePath $Edge -ArgumentList "--app=$Url", "--window-size=1060,720"
  exit 0
}

Start-Process $Url
