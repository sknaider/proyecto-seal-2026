param(
    [switch]$SelfTest,
    [switch]$NoLoop
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SealRoot = Resolve-Path (Join-Path $ScriptDir "..\..\..")
$WebChatUrl = $env:SEAL_WEBCHAT_URL
if (-not $WebChatUrl) { $WebChatUrl = "http://100.75.201.110:8765" }
$McpHealthUrl = $env:SEAL_MCP_HEALTH_URL
if (-not $McpHealthUrl) { $McpHealthUrl = "http://100.75.201.110:8771/health" }
$McpUrl = $env:SEAL_MCP_URL
if (-not $McpUrl) { $McpUrl = "http://100.75.201.110:8771/mcp" }

$env:SEAL_AGENT = "ADA"
$env:SEAL_ROOT = $SealRoot.Path
$env:SEAL_WEBCHAT_URL = $WebChatUrl
$env:SEAL_MCP_URL = $McpUrl
$env:SEAL_MCP_HEALTH_URL = $McpHealthUrl
$env:PATH = "$env:APPDATA\npm;$env:PATH"

Set-Location $SealRoot

function Test-CommandPresent {
    param([string]$Name)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "$Name not found in PATH"
    }
    return $cmd.Source
}

function Test-HttpJson {
    param([string]$Url)
    $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 6
    if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
        throw "HTTP $($response.StatusCode) for $Url"
    }
    return [string]$response.Content
}

function Send-AdaDm {
    param([string]$Message)
    $payload = @{
        from = "ADA"
        to = "William"
        type = "status"
        channel = "dm:ada:william"
        message = $Message
    } | ConvertTo-Json -Compress
    Invoke-WebRequest -UseBasicParsing -Uri "$WebChatUrl/api/agents/send" -Method Post -ContentType "application/json" -Body $payload -TimeoutSec 6 | Out-Null
}

Write-Host "ADA Codex visible Windows"
Write-Host "Root: $($SealRoot.Path)"
Write-Host "WebChat: $WebChatUrl"
Write-Host "MCP: $McpUrl"

$codexPath = Test-CommandPresent "codex"
Write-Host "codex: $codexPath"
Write-Host (& codex --version)

$chatHealth = Test-HttpJson "$WebChatUrl/api/health"
Write-Host "webchat health: $chatHealth"

$mcpHealth = Test-HttpJson $McpHealthUrl
Write-Host "mcp health: $mcpHealth"

$tmp = [System.IO.Path]::GetTempPath()
try {
    $catchup = Invoke-WebRequest -UseBasicParsing -Uri "$WebChatUrl/api/chat/messages/agent?agent=ADA&limit=30" -TimeoutSec 6
    Set-Content -Path (Join-Path $tmp "ada_codex_catchup.json") -Value $catchup.Content -Encoding UTF8
} catch {
    Set-Content -Path (Join-Path $tmp "ada_codex_catchup.json") -Value "{}" -Encoding UTF8
}

$presence = @"
[PRESENCIA ADA WINDOWS]
Host: dadito-laptop
Mode: Codex visible via Windows PowerShell
Canonical WebChat: $WebChatUrl
Canonical MCP: $McpUrl
WSL/tmux parity: unavailable until WSL2 virtualization starts
"@
Set-Content -Path (Join-Path $tmp "ada_codex_soul_presence.txt") -Value $presence -Encoding UTF8
Set-Content -Path (Join-Path $tmp "ada_codex_continuity.txt") -Value "dadito-laptop Windows visible launcher prepared via Tailscale." -Encoding UTF8

if ($SelfTest) {
    Write-Host "SELFTEST OK"
    exit 0
}

Send-AdaDm "ADA Codex visible Windows preparada en dadito-laptop - $((Get-Date).ToString('HH:mm:ss'))."

$PromptPath = Join-Path $ScriptDir "ADA_CODEX_BOOT_PROMPT.txt"
$BootPrompt = Get-Content -Raw -Path $PromptPath

$baseArgs = @()
if ($env:SEAL_CODEX_PROFILE) {
    $baseArgs += @("--profile", $env:SEAL_CODEX_PROFILE)
}
$baseArgs += @("--dangerously-bypass-approvals-and-sandbox")

do {
    & codex @baseArgs $BootPrompt
    $exitCode = $LASTEXITCODE
    if ($NoLoop -or $exitCode -eq 130 -or $exitCode -eq 143) {
        exit $exitCode
    }
    Write-Host "Codex exited with $exitCode. Restarting in 3 seconds..."
    Start-Sleep -Seconds 3
} while ($true)
