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
  [switch]$Uninstall,
  [switch]$Dev
)

$ErrorActionPreference = "Stop"
$ScriptRepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..") -ErrorAction SilentlyContinue).Path
$DefaultRoot = Join-Path $env:USERPROFILE ".hermes\hermes-chrome"
if ($ScriptRepoRoot -and (Test-Path (Join-Path $ScriptRepoRoot "bridge.py"))) {
  $Root = $ScriptRepoRoot
} else {
  $Root = $DefaultRoot
}
$RunDir = Join-Path $env:USERPROFILE ".hermes\run\hermes-chrome"
$EnvFile = Join-Path $RunDir "bridge.env"
$TaskName = "HermesChromeBridge"
$GrokConfig = Join-Path $env:USERPROFILE ".grok\config.toml"

function Write-Info($msg) { Write-Host "[hermes-chrome] $msg" }
function Die($msg) { Write-Error $msg; exit 1 }

function Ensure-RepoTree {
  if (Test-Path (Join-Path $Root "bridge.py")) {
    return
  }
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die "git is required to install Hermes Chrome companion into $Root"
  }
  $parent = Split-Path -Parent $Root
  if (-not (Test-Path $parent)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }
  if (Test-Path (Join-Path $Root ".git")) {
    Write-Info "updating existing checkout in $Root…"
    & git -C $Root pull --ff-only
  } else {
    Write-Info "cloning Hermes Chrome companion into $Root…"
    & git clone --depth 1 https://github.com/leaf76/hermes-chrome.git $Root
  }
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $Root "bridge.py"))) {
    Die "failed to populate $Root from GitHub"
  }
}

function Test-Python39([string]$Exe) {
  if (-not $Exe -or ($Exe -match 'WindowsApps') -or -not (Test-Path $Exe)) { return $false }
  try {
    & $Exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
    return ($LASTEXITCODE -eq 0)
  } catch {
    return $false
  }
}

function Find-Python {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    try {
      $p = & py -3 -c "import sys; print(sys.executable)" 2>$null
      if ($p) {
        $p = $p.Trim()
        if (Test-Python39 $p) { return $p }
      }
    } catch {}
  }
  foreach ($name in @("python", "python3")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and (Test-Python39 $cmd.Source)) {
      return $cmd.Source
    }
  }
  $paths = @(
    "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python39\python.exe",
    "C:\Python314\python.exe",
    "C:\Python313\python.exe",
    "C:\Python312\python.exe",
    "C:\Python311\python.exe"
  )
  foreach ($p in $paths) {
    if (Test-Python39 $p) { return $p }
  }
  $hermesPy = Get-ChildItem "$env:LOCALAPPDATA\hermes\tools\python-*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
  if ($hermesPy -and (Test-Python39 $hermesPy.FullName)) { return $hermesPy.FullName }
  return $null
}

function Ensure-Token {
  New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
  if (Test-Path $EnvFile) { return }
  $bytes = New-Object byte[] 32
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $rng.GetBytes($bytes)
  } finally {
    $rng.Dispose()
  }
  $token = [Convert]::ToBase64String($bytes).TrimEnd('=') -replace '\+', '-' -replace '/', '_'
  $content = "# Hermes Chrome bridge auth (local only). Auto-managed; do not commit.`r`nexport HERMES_CHROME_BRIDGE_TOKEN='$token'`r`n"
  [System.IO.File]::WriteAllText($EnvFile, $content, (New-Object System.Text.UTF8Encoding($false)))
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
  $userId = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
  $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
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
  try {
    $h = Invoke-WebRequest -Uri "http://127.0.0.1:19876/v1/health" -UseBasicParsing -TimeoutSec 2
    if ($h.StatusCode -eq 200) {
      Write-Info "bridge already healthy on :19876"
      return
    }
  } catch {}

  $log = Join-Path $RunDir "bridge.windows.out.log"
  $errLog = Join-Path $RunDir "bridge.windows.err.log"
  $prevRun = $env:HERMES_CHROME_RUN
  $env:HERMES_CHROME_RUN = $RunDir
  try {
    $p = Start-Process `
      -FilePath $PythonExe `
      -ArgumentList "`"$BridgePy`"" `
      -WorkingDirectory $Root `
      -WindowStyle Hidden `
      -RedirectStandardOutput $log `
      -RedirectStandardError $errLog `
      -PassThru
  } finally {
    if ($null -eq $prevRun) {
      Remove-Item Env:\HERMES_CHROME_RUN -ErrorAction SilentlyContinue
    } else {
      $env:HERMES_CHROME_RUN = $prevRun
    }
  }
  if ($p) {
    [System.IO.File]::WriteAllText((Join-Path $RunDir "bridge.pid"), "$($p.Id)", (New-Object System.Text.UTF8Encoding($false)))
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
  Die "bridge failed to become healthy — see $errLog / $log"
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
  $installer = Join-Path $Root "scripts\install-mcp.py"
  if (-not (Test-Path $installer)) {
    Write-Info "skip MCP (missing install-mcp.py)"
    return
  }
  Write-Info "registering MCP (Grok / Cursor / Claude Desktop / VS Code when present)…"
  & $PythonExe $installer --root $Root --python $PythonExe
  if ($LASTEXITCODE -ne 0) {
    Write-Info "MCP registration returned $LASTEXITCODE (partial ok)"
  }
}

# ---- main ----
if ($Uninstall) {
  Uninstall-Task
  $nm = Join-Path $Root "scripts\install-native-host.ps1"
  if (-not (Test-Path $nm) -and $PSScriptRoot) {
    $nm = Join-Path $PSScriptRoot "install-native-host.ps1"
  }
  if (Test-Path $nm) {
    & powershell -ExecutionPolicy Bypass -File $nm -Uninstall
  }
  $pyUn = Find-Python
  $mcpInstaller = Join-Path $Root "scripts\install-mcp.py"
  if ($pyUn -and (Test-Path $mcpInstaller)) {
    & $pyUn $mcpInstaller --uninstall
  }
  $pidFile = Join-Path $RunDir "bridge.pid"
  if (Test-Path $pidFile) {
    $pidText = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($pidText -and ($pidText.Trim() -match '^\d+$')) {
      Stop-Process -Id ([int]$pidText.Trim()) -Force -ErrorAction SilentlyContinue
      Write-Info "stopped bridge process $($pidText.Trim())"
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
  }
  $autoOn = Join-Path $RunDir "auto-update.on"
  if (Test-Path $autoOn) { Remove-Item $autoOn -Force -ErrorAction SilentlyContinue }
  Write-Info "uninstall complete (bridge.env token preserved at $EnvFile)"
  exit 0
}

Ensure-RepoTree
$BridgePy = Join-Path $Root "bridge.py"
$McpPy = Join-Path $Root "mcp_server.py"
if (-not (Test-Path $BridgePy)) { Die "missing $BridgePy" }
if (-not (Test-Path $McpPy)) { Die "missing $McpPy" }

$py = Find-Python
if (-not $py) {
  Die @"
No real Python 3.9+ found (Windows Store stub does not count).

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
$nm = Join-Path $Root "scripts\install-native-host.ps1"
if (Test-Path $nm) {
  Write-Info "installing Chrome Native Messaging host…"
  & powershell -ExecutionPolicy Bypass -File $nm
} else {
  Write-Info "skip native host (missing install-native-host.ps1)"
}

if (-not $SkipGrok) { Install-McpAll -PythonExe $py }

$doctor = Join-Path $Root "scripts\doctor.py"
if (Test-Path $doctor) {
  Write-Info "running doctor…"
  & $py $doctor
}

$upd = Join-Path $Root "lib\self_update.py"
if (-not $Dev -and (Test-Path $upd) -and (Test-Path (Join-Path $Root ".git"))) {
  Write-Info "enabling companion auto-update (git ff-only; disable: lib\self_update.py disable)"
  & $py $upd enable
} else {
  Write-Info "skipping auto-update (-Dev or non-git install)"
}

Write-Host ""
Write-Host "=== Companion (machine half) installed ===" -ForegroundColor Cyan
Write-Host "Root:    $Root"
Write-Host "Runtime: $RunDir"
Write-Host "MCP:     $McpPy"
Write-Host "This was step 1 of 2. Extension alone is never enough."
Write-Host "Not on npm — companion is this tree (GitHub)."
Write-Host ""
Write-Host "=== Next: browser half (extension v1.8+ — CWS or Load unpacked) ===" -ForegroundColor Cyan
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
