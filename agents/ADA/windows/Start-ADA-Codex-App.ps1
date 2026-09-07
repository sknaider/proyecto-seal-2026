param(
    [switch]$SelfTest,
    [switch]$Launch
)

$ErrorActionPreference = "Stop"

$AppId = "OpenAI.Codex_2p2nqsd0c76g0!App"
$Workspace = "C:\Users\Dadito\IA\proyecto-seal"
$WebChatUrl = $env:SEAL_WEBCHAT_URL
if (-not $WebChatUrl) { $WebChatUrl = "http://100.75.201.110:8765" }
$McpHealthUrl = $env:SEAL_MCP_HEALTH_URL
if (-not $McpHealthUrl) { $McpHealthUrl = "http://100.75.201.110:8771/health" }

function Test-HttpJson {
    param([string]$Url)
    $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 6
    if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
        throw "HTTP $($response.StatusCode) for $Url"
    }
    return [string]$response.Content
}

function Test-CodexApp {
    $app = Get-StartApps | Where-Object { $_.AppID -eq $AppId } | Select-Object -First 1
    if (-not $app) {
        throw "Codex App not found: $AppId"
    }
    return $app
}

function Test-CodexProjectTrusted {
    $configPath = Join-Path $env:USERPROFILE ".codex\config.toml"
    if (-not (Test-Path $configPath)) {
        throw "Codex config not found: $configPath"
    }
    $config = Get-Content -Raw -Path $configPath
    if ($config -notmatch "\[projects\.'c:\\users\\dadito\\ia\\proyecto-seal'\]") {
        throw "Codex project trust missing for c:\users\dadito\ia\proyecto-seal"
    }
    return $configPath
}

function New-AdaCodexAppShortcut {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $linkPath = Join-Path $desktop "ADA Codex App.lnk"
    $wsh = New-Object -ComObject WScript.Shell
    $shortcut = $wsh.CreateShortcut($linkPath)
    $shortcut.TargetPath = "$env:WINDIR\explorer.exe"
    $shortcut.Arguments = "shell:AppsFolder\$AppId"
    $shortcut.WorkingDirectory = $Workspace
    $shortcut.Description = "Open Codex App for ADA SEAL workspace"
    $shortcut.Save()
    return $linkPath
}

Write-Host "ADA Codex App Windows"
Write-Host "Workspace: $Workspace"
Write-Host "AppID: $AppId"

$app = Test-CodexApp
Write-Host "codex app: $($app.Name) / $($app.AppID)"

$configPath = Test-CodexProjectTrusted
Write-Host "trusted project config: $configPath"

$chatHealth = Test-HttpJson "$WebChatUrl/api/health"
Write-Host "webchat health: $chatHealth"

$mcpHealth = Test-HttpJson $McpHealthUrl
Write-Host "mcp health: $mcpHealth"

$shortcutPath = New-AdaCodexAppShortcut
Write-Host "shortcut: $shortcutPath"

if ($SelfTest) {
    Write-Host "SELFTEST OK"
    exit 0
}

if ($Launch) {
    Start-Process "$env:WINDIR\explorer.exe" "shell:AppsFolder\$AppId"
}
