param(
    [switch]$SelfTest
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

Write-Host "ADA Claude Code Windows"
Write-Host "Root: $($SealRoot.Path)"

$claudePath = Test-CommandPresent "claude"
Write-Host "claude: $claudePath"
Write-Host (& claude --version)

$chatHealth = Test-HttpJson "$WebChatUrl/api/health"
Write-Host "webchat health: $chatHealth"

$mcpHealth = Test-HttpJson $McpHealthUrl
Write-Host "mcp health: $mcpHealth"

if ($SelfTest) {
    Write-Host "SELFTEST OK"
    exit 0
}

Send-AdaDm "ADA Claude Code interface preparada en dadito-laptop - $((Get-Date).ToString('HH:mm:ss'))."
& claude
