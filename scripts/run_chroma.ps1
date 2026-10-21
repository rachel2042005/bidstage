<#
.SYNOPSIS
    Starts the Chroma vector store in server mode.

.DESCRIPTION
    Chroma is thread-safe but not process-safe (ENV-4). Flask and the agent
    both need the store, so it runs as its own process and both connect over
    HTTP. Nothing in the application may open the data directory directly.

    Leave this window open. Start it before main.py, agent/runner.py or
    tests/smoke_test_vector_store.py.
#>

#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$DataPath = Join-Path $RepoRoot "chroma-data"
$Port = 8000

function Resolve-Chroma {
    # The project venv first, so the server always matches the pinned
    # chromadb in requirements.txt even when the venv is not activated.
    $inVenv = Join-Path $RepoRoot ".venv\Scripts\chroma.exe"
    if (Test-Path $inVenv) { return $inVenv }

    $onPath = Get-Command chroma -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    return $null
}

$Chroma = Resolve-Chroma
if (-not $Chroma) {
    Write-Error "chroma was not found. Run: pip install -r requirements.txt"
    exit 1
}

# A second server on the same directory is the failure this script exists to
# prevent: it corrupts the SQLite file rather than reporting a clean error.
$inUse = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($inUse) {
    Write-Host "Chroma is already listening on port $Port. Nothing to do." -ForegroundColor Yellow
    exit 0
}

if (-not (Test-Path $DataPath)) {
    New-Item -ItemType Directory -Path $DataPath | Out-Null
    Write-Host "Created $DataPath"
}

Write-Host "Chroma starting on http://localhost:$Port"
Write-Host "  data: $DataPath (gitignored; rebuilt by the seed script)"
Write-Host "Leave this window open." -ForegroundColor Cyan

& $Chroma run --path $DataPath --port $Port
