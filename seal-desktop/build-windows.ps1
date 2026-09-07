param(
  [ValidateSet("x64", "arm64")]
  [string]$Arch = "x64"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Target = if ($Arch -eq "arm64") { "aarch64-pc-windows-msvc" } else { "x86_64-pc-windows-msvc" }

Write-Host "[windows] validating toolchain"
node --version | Out-Host
npm --version | Out-Host
rustc --version | Out-Host
cargo --version | Out-Host
rustup target add $Target | Out-Host

Write-Host "[windows] building companion UI"
npm --prefix "$Root\companion_core\ui" install
npm --prefix "$Root\companion_core\ui" run build

Write-Host "[windows] building Tauri exe/installer for $Target"
npm --prefix "$Root" run tauri -- build --target $Target --config src-tauri/tauri.user.conf.json

Write-Host "[windows] artifacts"
$Bundle = Join-Path $Root "src-tauri\target\$Target\release\bundle"
if (Test-Path $Bundle) {
  Get-ChildItem $Bundle -Recurse -File |
    Where-Object { $_.Extension -in ".exe", ".msi" -or $_.FullName -match "nsis|msi" } |
    Select-Object FullName, Length |
    Format-Table -AutoSize
} else {
  throw "Bundle directory not found: $Bundle"
}
