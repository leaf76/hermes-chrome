#Requires -Version 5.1
<#
.SYNOPSIS
  One-shot Windows install: local bridge autostart + optional Grok MCP registration.

.DESCRIPTION
  Hermes Chrome cannot work from the extension alone — this installs the missing
  local control plane so agents (Grok / Cursor / Claude) can drive Chrome.

  Steps:
    1) Find a real Python 3 (not Windows Store stub)
    2) Ensure ~/.hermes/run/hermes-chrome + bridge.env token
    3) Register a per-user Scheduled Task to keep bridge.py on :19876
    4) Start bridge now + open pairing window
    5) Optionally write [mcp_servers.hermes-chrome] into ~/.grok/config.toml

.PARAMETER SkipGrok
  Do not modify Grok config.

.PARAMETER Uninstall
  Remove scheduled task and (optionally) leave MCP entry disabled.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1
#>
param(
  [switch]$SkipGrok,
  [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BridgePy = Join-Path $Root "bridge.py"
$McpPy = Join-Path $Root "mcp_server.py"
$RunDir = Join-Path $env:USERPROFILE ".hermes\run\hermes-chrome"
$EnvFile = Join-Path $RunDir "bridge.env"
$TaskName = "HermesChromeBridge"
$GrokConfig = Join-Path $env:USERPROFILE ".grok\config.toml"

function Write-Info($msg) { Write-Host "[hermes-chrome] $msg" }
function Die($msg) { Write-Error $msg; exit 1 }

function Find-Python {
  $candidates = @()
  if (Get-Command py -ErrorAction SilentlyContinue) {
    try {
      $p = & py -3 -c "import sys; print(sys.executable)" 2>$null
      if ($p -and ($p -notmatch 'WindowsApps')) { return $p.Trim() }
    } catch {}
  }
  foreach ($name in @("python", "python3")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and ($cmd.Source -notmatch 'WindowsApps')) {
      return $cmd.Source
    }
  }
  $paths = @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "C:\Python312\python.exe",
    "C:\Python311\python.exe"
  )
  foreach ($p in $paths) {
    if (Test-Path $p) { return $p }
  }
  return $null
}

function Ensure-Token {
  New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
  if (Test-Path $EnvFile) { return }
  $bytes = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $token = [Convert]::ToBase64String($bytes).TrimEnd('=') -replace '\+', '-' -replace '/', '_'
  @"
# Hermes Chrome bridge auth (local only). Auto-managed; do not commit.
export HERMES_CHROME_BRIDGE_TOKEN='$token'
"@ | Set-Content -Path $EnvFile -Encoding UTF8
  Write-Info "wrote token: $EnvFile"
}

function Uninstall-Task {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  Write-Info "scheduled task removed: $TaskName"
}

function Install-Task([string]$PythonExe) {
  Uninstall-Task
  $arg = "`"$BridgePy`""
  $action = New-ScheduledTaskAction -Execute $PythonExe -Argument $arg -WorkingDirectory $Root
  $trigger = New-ScheduledTaskTrigger -AtLogOn
  $settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
  $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Hermes Chrome local bridge (127.0.0.1:19876)" | Out-Null
  Write-Info "scheduled task installed: $TaskName (AtLogOn)"
}

function Start-BridgeNow([string]$PythonExe) {
  # Kill anything already on 19876? leave alone if healthy.
  try {
    $h = Invoke-WebRequest -Uri "http://127.0.0.1:19876/v1/health" -UseBasicParsing -TimeoutSec 2
    if ($h.StatusCode -eq 200) {
      Write-Info "bridge already healthy on :19876"
      return
    }
  } catch {}

  $log = Join-Path $RunDir "bridge.windows.log"
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $PythonExe
  $psi.Arguments = "`"$BridgePy`""
  $psi.WorkingDirectory = $Root
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.Environment["HERMES_CHROME_RUN"] = $RunDir
  $p = [System.Diagnostics.Process]::Start($psi)
  if ($p) {
    Set-Content -Path (Join-Path $RunDir "bridge.pid") -Value $p.Id -Encoding ascii
  }
  for ($i = 0; $i -lt 40; $i++) {
    try {
      $h = Invoke-WebRequest -Uri "http://127.0.0.1:19876/v1/health" -UseBasicParsing -TimeoutSec 1
      if ($h.StatusCode -eq 200) {
        Write-Info "bridge started (pid=$($p.Id))"
        return
      }
    } catch {}
    Start-Sleep -Milliseconds 150
  }
  Die "bridge failed to become healthy — see Python console / $log"
}

function Open-Pairing {
  $tok = $null
  if (Test-Path $EnvFile) {
    foreach ($line in Get-Content $EnvFile) {
      if ($line -match "HERMES_CHROME_BRIDGE_TOKEN='([^']+)'") { $tok = $Matches[1]; break }
      if ($line -match 'HERMES_CHROME_BRIDGE_TOKEN="([^"]+)"') { $tok = $Matches[1]; break }
      if ($line -match "HERMES_CHROME_BRIDGE_TOKEN=(.+)$") { $tok = $Matches[1].Trim(); break }
    }
  }
  $headers = @{ "Content-Type" = "application/json" }
  if ($tok) { $headers["X-Hermes-Chrome-Token"] = $tok }
  try {
    Invoke-WebRequest -Uri "http://127.0.0.1:19876/v1/pair-open" -Method POST -Headers $headers -Body "{}" -UseBasicParsing -TimeoutSec 5 | Out-Null
    Write-Info "pairing window open (extension should auto-pair within ~5 min)"
  } catch {
    Write-Info "pair-open failed (bridge may still be starting): $($_.Exception.Message)"
  }
}

function Install-McpAll([string]$PythonExe) {
  $installer = Join-Path $PSScriptRoot "install-mcp.py"
  if (-not (Test-Path $installer)) {
    Write-Info "skip MCP (missing install-mcp.py)"
    return
  }
  Write-Info "registering MCP (Grok / Cursor / Claude Desktop when present)…"
  & $PythonExe $installer --root $Root --python $PythonExe
  if ($LASTEXITCODE -ne 0) {
    Write-Info "MCP registration returned $LASTEXITCODE (partial ok)"
  }
}

# ---- main ----
if (-not (Test-Path $BridgePy)) { Die "missing $BridgePy" }
if (-not (Test-Path $McpPy)) { Die "missing $McpPy" }

if ($Uninstall) {
  Uninstall-Task
  Write-Info "bridge process not killed (stop manually if needed)"
  Write-Info "Grok MCP entry left in place; remove [mcp_servers.hermes-chrome] from config.toml if desired"
  exit 0
}

$py = Find-Python
if (-not $py) {
  Die @"
No real Python 3 found (Windows Store stub does not count).

Install Python 3.11+ from https://www.python.org/downloads/
  - check 'Add python.exe to PATH'
  - then re-run this script

"@
}

Write-Info "python: $py"
Write-Info "repo:   $Root"
Ensure-Token
Install-Task -PythonExe $py
Start-BridgeNow -PythonExe $py
Open-Pairing

# Native Messaging — extension can auto-start bridge on click/reload
$nm = Join-Path $PSScriptRoot "install-native-host.ps1"
if (Test-Path $nm) {
  Write-Info "installing Chrome Native Messaging host…"
  & powershell -ExecutionPolicy Bypass -File $nm
} else {
  Write-Info "skip native host (missing install-native-host.ps1)"
}

if (-not $SkipGrok) { Install-McpAll -PythonExe $py }

$doctor = Join-Path $PSScriptRoot "doctor.py"
if (Test-Path $doctor) {
  Write-Info "running doctor…"
  & $py $doctor
}

$upd = Join-Path $Root "lib\self_update.py"
if (Test-Path $upd) {
  Write-Info "enabling companion auto-update (git ff-only; disable: hermes-chrome self-update disable)"
  & $py $upd enable
}

Write-Host ""
Write-Host "=== Companion (machine half) installed ===" -ForegroundColor Cyan
Write-Host "Root:    $Root"
Write-Host "Runtime: $RunDir"
Write-Host "MCP:     $McpPy"
Write-Host "This was step 1 of 2. Extension alone is never enough."
Write-Host "Not on npm — companion is this tree (GitHub)."
Write-Host ""
Write-Host "=== Next: browser half (extension v1.7+ — CWS or Load unpacked) ===" -ForegroundColor Cyan
Write-Host "1. Install/enable Hermes Chrome (nativeMessaging permission)"
Write-Host "2. Reload extension → click icon once (native host starts bridge)"
Write-Host "3. Wait for auto-pair, or click Pair — Ready: popup says Connected"
Write-Host "4. Restart MCP agents (Grok/Cursor/Claude) if you use tools"
Write-Host ""
Write-Host "Smoke:"
Write-Host "  Invoke-RestMethod http://127.0.0.1:19876/v1/health"
Write-Host "  $py $doctor"
Write-Host "  # CLI: scripts\hermes-chrome.sh --json ping"
Write-Host "  # MCP snippet: $RunDir\mcp-snippet.json"
Write-Host ""
Write-Info "done."
