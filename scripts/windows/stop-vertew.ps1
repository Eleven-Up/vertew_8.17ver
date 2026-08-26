# Stops whatever start-vertew.ps1 last started (tracked in vertew-run.json).
# Safe to run even if nothing is running.

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$runFile = Join-Path $root "vertew-run.json"
$urlFile = Join-Path $root "vertew-url.txt"

if (-not (Test-Path $runFile)) {
    Write-Host "Nothing tracked as running (no vertew-run.json). Nothing to stop." -ForegroundColor DarkGray
    exit 0
}

$run = Get-Content $runFile -Raw | ConvertFrom-Json
$stoppedAny = $false

foreach ($entry in @(
    @{ name = "backend server"; procId = $run.serverPid },
    @{ name = "cloudflared tunnel"; procId = $run.tunnelPid }
)) {
    if (-not $entry.procId) { continue }
    $proc = Get-Process -Id $entry.procId -ErrorAction SilentlyContinue
    if ($proc) {
        Stop-Process -Id $entry.procId -Force
        Write-Host "Stopped $($entry.name) (PID $($entry.procId))." -ForegroundColor Green
        $stoppedAny = $true
    }
}

if (-not $stoppedAny) {
    Write-Host "Tracked processes were already gone." -ForegroundColor DarkGray
}

Remove-Item $runFile -Force -ErrorAction SilentlyContinue
Remove-Item $urlFile -Force -ErrorAction SilentlyContinue
Write-Host "Vertew stopped."
