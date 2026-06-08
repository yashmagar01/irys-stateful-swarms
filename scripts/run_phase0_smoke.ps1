# Phase 0 Smoke Run — 48-task evaluation with all features ON
# Run from repo root: .\scripts\run_phase0_smoke.ps1

param(
    [string]$RunLabel = "phase0_all_features",
    [string]$EnvFile = ".env.phase0",
    [int]$Concurrency = 48,
    [switch]$ScoreOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $repoRoot

$manifest = "benchmarks\manifests\phase0_smoke_48.json"
$resultsDir = "results\$RunLabel"

if (-not (Test-Path $manifest)) {
    Write-Error "Manifest not found: $manifest"
    exit 1
}

# Load env file
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | Where-Object { $_ -match '^[A-Z]' } | ForEach-Object {
        $parts = $_ -split '=', 2
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
        Write-Host "  SET $($parts[0].Trim())=$($parts[1].Trim())"
    }
} else {
    Write-Warning "No env file: $EnvFile (running with defaults)"
}

# Must have API key
if (-not ($env:GEMINI_API_KEY -or $env:GOOGLE_API_KEY)) {
    Write-Error "No API key. Set GEMINI_API_KEY or GOOGLE_API_KEY."
    exit 1
}

# Must have bench root
if (-not $env:HARVEY_BENCH_ROOT) {
    $env:HARVEY_BENCH_ROOT = "C:\Users\devan\OneDrive\Desktop\Projects\harvey-labs"
}

Write-Host "`n=== Phase 0 Smoke: $RunLabel ==="
Write-Host "  Manifest: $manifest"
Write-Host "  Results:  $resultsDir"
Write-Host "  Concurrency: $Concurrency"
Write-Host "  Bench root: $env:HARVEY_BENCH_ROOT"
Write-Host ""

if (-not $ScoreOnly) {
    Write-Host "--- Running batch ---"
    $t0 = Get-Date
    python -m src.cli batch $manifest -o $resultsDir -j $Concurrency
    $elapsed = (Get-Date) - $t0
    Write-Host "`nBatch done in $([math]::Round($elapsed.TotalMinutes, 1)) minutes"
}

Write-Host "`n--- Scoring ---"
python -m src.cli score $resultsDir --bench-root $env:HARVEY_BENCH_ROOT -j 20 --task-concurrency 5

Write-Host "`n--- Analysis ---"
python -m src.cli analyze $resultsDir

Write-Host "`n--- Lifecycle summary ---"
python -m src.cli summarize-lifecycle $resultsDir

Write-Host "`nDone: $RunLabel"
