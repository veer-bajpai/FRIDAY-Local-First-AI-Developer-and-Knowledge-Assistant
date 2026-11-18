# FRIDAY one-command installer for Windows (PowerShell).
#   irm https://raw.githubusercontent.com/veer-bajpai/FRIDAY-Local-First-AI-Knowledge-Assistant/main/install.ps1 | iex
# or, from a downloaded copy: powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"

$RepoUrl = if ($env:FRIDAY_REPO) { $env:FRIDAY_REPO } else { "https://github.com/veer-bajpai/FRIDAY-Local-First-AI-Knowledge-Assistant.git" }
$Branch = if ($env:FRIDAY_BRANCH) { $env:FRIDAY_BRANCH } else { "main" }
$HomeDir = Join-Path $env:USERPROFILE ".friday"
$BinDir = Join-Path $HomeDir "bin"

function Info($message) { Write-Host "▸ $message" -ForegroundColor Cyan }
function Ok($message) { Write-Host "✔ $message" -ForegroundColor Green }
function Warn($message) { Write-Host "! $message" -ForegroundColor Yellow }
function Die($message) { Write-Host "✖ $message" -ForegroundColor Red; exit 1 }
function Have($commandName) { [bool](Get-Command $commandName -ErrorAction SilentlyContinue) }
function Refresh-Path {
  $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = $machinePath + ";" + $userPath
}
function Node-Ok {
  if (-not (Have node)) { return $false }
  try {
    $nodeVersion = node --version
    if ($LASTEXITCODE -ne 0) { return $false }
    $nodeMajorText = (($nodeVersion -replace '^v', '') -split '\.')[0]
    $nodeMajor = [int]$nodeMajorText
    return $nodeMajor -ge 20
  } catch { return $false }
}
function Python-Ok {
  foreach ($candidate in @("py", "python", "python3")) {
    if (Have $candidate) {
      try {
        $version = (& $candidate --version 2>&1 | Out-String)
        if ($version -match "Python 3\.(1[1-9]|[2-9]\d)") { return $true }
      } catch {}
    }
  }
  return $false
}
function Winget-Install($id, $name) {
  Info "Installing $name…"
  winget install -e --id $id --accept-package-agreements --accept-source-agreements --silent
  if ($LASTEXITCODE -ne 0) { Warn "winget reported a problem installing $name (code $LASTEXITCODE)." }
  Refresh-Path
}

if (-not (Have winget)) { Die "winget was not found. Install App Installer, then run this again." }
if (-not (Have git)) { Winget-Install "Git.Git" "Git" }
if (-not (Node-Ok)) { Winget-Install "OpenJS.NodeJS.LTS" "Node.js" }
if (-not (Python-Ok)) { Winget-Install "Python.Python.3.12" "Python 3.12" }
if (-not (Have ollama)) { Winget-Install "Ollama.Ollama" "Ollama" }

Refresh-Path
if (-not (Have git)) { Die "Git was not found after installation. Open a new PowerShell and run the installer again." }
if (-not (Node-Ok)) { Die "Node.js 20+ was not found after installation. Open a new PowerShell and run the installer again." }
if (-not (Python-Ok)) { Die "Python 3.11+ was not found after installation. Open a new PowerShell and run the installer again." }
if (-not (Have ollama)) { Warn "Ollama is not on PATH yet; a new terminal may be required." }

if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "bin\friday.js"))) {
  $AppDir = $PSScriptRoot
  Info "Using project at $AppDir"
} else {
  $AppDir = Join-Path $HomeDir "app"
  New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null
  if (Test-Path (Join-Path $AppDir ".git")) {
    Info "Updating FRIDAY…"
    git -C $AppDir pull --ff-only
    if ($LASTEXITCODE -ne 0) { Die "Could not update the FRIDAY checkout." }
  } else {
    Info "Downloading FRIDAY…"
    git clone --depth 1 --branch $Branch $RepoUrl $AppDir
    if ($LASTEXITCODE -ne 0) { Die "Could not download the FRIDAY repository." }
  }
}

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$shim = "@echo off`r`nnode `"$AppDir\bin\friday.js`" %*`r`n"
Set-Content -Path (Join-Path $BinDir "friday.cmd") -Value $shim -Encoding ASCII

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$entries = @($userPath -split ";" | Where-Object { $_ })
if (-not ($entries | Where-Object { $_.TrimEnd('\') -ieq $BinDir.TrimEnd('\') })) {
  $newUserPath = $BinDir
  if (-not [string]::IsNullOrWhiteSpace($userPath)) { $newUserPath = $userPath + ";" + $BinDir }
  [Environment]::SetEnvironmentVariable("Path", $newUserPath, "User")
}
$env:Path = $env:Path + ";" + $BinDir

Info "Setting up FRIDAY (dependencies, build, and AI models)…"
& (Join-Path $BinDir "friday.cmd") setup
if ($LASTEXITCODE -ne 0) { Die "Setup did not finish. Check the messages above." }

Write-Host ""
Ok "FRIDAY is installed. Open a NEW terminal and run:"
Write-Host "  friday browser    run in your browser"
Write-Host "  friday chat       run in this terminal"
Write-Host "  friday            choose a mode"
