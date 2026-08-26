# Starts the Vertew backend (serving all three frontends) plus a Cloudflare
# quick tunnel, waits for both to come up, and prints the local + public URLs.
# Safe to re-run: it first stops whatever a previous run of this script left
# behind (tracked in vertew-run.json), so it never piles up stray processes.
#
# Requires: backend/.venv already created with backend/requirements.txt
# installed, and the frontend/apps builds already in place (see README.md
# "Local setup"). Requires cloudflared on PATH for the public URL; without it,
# only the local URL is printed.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

$runFile = Join-Path $root "vertew-run.json"
$serverLog = Join-Path $root "backend\server_run.log"
$serverErrLog = Join-Path $root "backend\server_run.err.log"
$tunnelLog = Join-Path $root "backend\cloudflared.log"
$tunnelErrLog = Join-Path $root "backend\cloudflared.err.log"
$urlFile = Join-Path $root "vertew-url.txt"

function Stop-TrackedRun {
    if (-not (Test-Path $runFile)) { return }
    try {
        $prev = Get-Content $runFile -Raw | ConvertFrom-Json
        foreach ($procId in @($prev.serverPid, $prev.tunnelPid)) {
            if ($procId) {
                try { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue } catch {}
            }
        }
    } catch {
        # Malformed/leftover file from an older script version -- ignore.
    }
    Remove-Item $runFile -Force -ErrorAction SilentlyContinue
}

Write-Host "Stopping any previous run..." -ForegroundColor DarkGray
Stop-TrackedRun

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "No .venv found at $venvPython." -ForegroundColor Red
    Write-Host "Set it up first: python -m venv .venv; .venv\Scripts\pip install -r backend\requirements.txt" -ForegroundColor Red
    exit 1
}

Write-Host "Starting backend server..." -ForegroundColor Cyan
Remove-Item $serverLog, $serverErrLog -Force -ErrorAction SilentlyContinue
# No --reload here: this launcher is for quickly getting a demo/testing
# session up, not active development (--reload's child-process reloader is
# also awkward to track/stop cleanly from a script). Use the manual `uvicorn
# --reload` workflow from README.md instead while actively coding.
$serverProc = Start-Process -FilePath $venvPython `
    -ArgumentList "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000" `
    -WorkingDirectory (Join-Path $root "backend") `
    -RedirectStandardOutput $serverLog -RedirectStandardError $serverErrLog `
    -WindowStyle Hidden -PassThru

$serverReady = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/stores/demo/menu" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) { $serverReady = $true; break }
    } catch {}
    if ($serverProc.HasExited) { break }
}

if (-not $serverReady) {
    Write-Host "Server did not come up. Check backend\server_run.log / server_run.err.log:" -ForegroundColor Red
    if (Test-Path $serverErrLog) { Get-Content $serverErrLog -Tail 30 -ErrorAction SilentlyContinue }
    if (-not $serverProc.HasExited) { Stop-Process -Id $serverProc.Id -Force -ErrorAction SilentlyContinue }
    exit 1
}

Write-Host "Backend ready: http://localhost:8000" -ForegroundColor Green

$tunnelProc = $null
$publicUrl = $null
$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if ($cloudflared) {
    Write-Host "Starting Cloudflare tunnel..." -ForegroundColor Cyan
    Remove-Item $tunnelLog, $tunnelErrLog -Force -ErrorAction SilentlyContinue
    $tunnelProc = Start-Process -FilePath $cloudflared.Source `
        -ArgumentList "tunnel", "--url", "http://localhost:8000" `
        -RedirectStandardOutput $tunnelLog -RedirectStandardError $tunnelErrLog `
        -WindowStyle Hidden -PassThru

    function Read-SharedText([string]$path) {
        # The child process still has this file open for writing (via
        # Start-Process redirection), so a plain exclusive read fails while
        # it's live -- open with FileShare.ReadWrite instead.
        if (-not (Test-Path $path)) { return "" }
        try {
            $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
            try {
                $reader = New-Object IO.StreamReader($stream)
                return $reader.ReadToEnd()
            } finally {
                $stream.Dispose()
            }
        } catch {
            return ""
        }
    }

    $urlPattern = [regex]"https://[a-zA-Z0-9\-]+\.trycloudflare\.com"
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        # cloudflared logs its startup banner (including the assigned URL) to
        # stderr, not stdout -- check both rather than relying on one.
        $content = (Read-SharedText $tunnelLog) + (Read-SharedText $tunnelErrLog)
        $match = $urlPattern.Match($content)
        if ($match.Success) {
            $publicUrl = $match.Value
            break
        }
        if ($tunnelProc.HasExited) { break }
    }

    if (-not $publicUrl) {
        Write-Host "Tunnel did not report a URL in time -- check backend\cloudflared.err.log." -ForegroundColor Yellow
    }
} else {
    Write-Host "cloudflared not found on PATH -- skipping the public tunnel (local URL still works)." -ForegroundColor Yellow
}

@{
    serverPid = $serverProc.Id
    tunnelPid = if ($tunnelProc) { $tunnelProc.Id } else { $null }
    url       = $publicUrl
} | ConvertTo-Json | Set-Content -Path $runFile

if ($publicUrl) { Set-Content -Path $urlFile -Value $publicUrl } else { Remove-Item $urlFile -Force -ErrorAction SilentlyContinue }

Write-Host ""
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host " Vertew is running"
Write-Host " Hologram (local):  http://localhost:8000/?display=hologram"
Write-Host " Customer (local):  http://localhost:8000/order/store/demo"
Write-Host " Vendor (local):    http://localhost:8000/vendor?store=demo"
if ($publicUrl) {
    Write-Host " Public (anyone):   $publicUrl"
} else {
    Write-Host " Public URL:        unavailable (see warning above)"
}
Write-Host "================================================="
Write-Host ""
Write-Host "Run stop-vertew.bat when you're done."
