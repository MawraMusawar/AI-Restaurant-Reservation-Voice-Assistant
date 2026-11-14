<#
.SYNOPSIS
  Start the demo: pre-flight checks, then the backend and frontend in their own windows.

.EXAMPLE
  .\start-demo.ps1                   # checks + start
  .\start-demo.ps1 -RefreshSamples   # also re-date the sample reservations to today
  .\start-demo.ps1 -SkipChecks       # just start the servers
  .\start-demo.ps1 -Dev              # frontend in dev mode (skips the ~1-2 min production build)

  If Windows blocks the script: powershell -ExecutionPolicy Bypass -File .\start-demo.ps1
#>
param(
    [switch]$RefreshSamples,
    [switch]$SkipChecks,
    [int]$BackendPort = 0,    # default: from frontend/.env.local BACKEND_URL, else 8000
    [int]$WebPort = 3000,
    [switch]$NoBrowser,
    [switch]$Dev
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'

function Say($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }

# Run `python -m <module>` in backend/. Its log goes to stderr, which isn't a failure here;
# the exit code says whether it worked.
function Invoke-Backend([string[]]$Arguments) {
    Push-Location $backend
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & uv run python -m @Arguments 2>&1 | ForEach-Object { Write-Host "   $_" } }
    finally { $ErrorActionPreference = $previous; Pop-Location }
    return $LASTEXITCODE
}

# The backend port comes from frontend/.env.local (BACKEND_URL), default 8000.
$port = if ($BackendPort) { $BackendPort } else { 8000 }
$envLocal = Join-Path $frontend '.env.local'
if (-not $BackendPort -and (Test-Path $envLocal)) {
    $m = Select-String -Path $envLocal -Pattern '^\s*BACKEND_URL\s*=\s*https?://[^:/]+:(\d+)' |
        Select-Object -First 1
    if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
}

if (-not (Test-Path (Join-Path $backend '.env'))) {
    Say 'backend\.env is missing. Copy backend\.env.example to backend\.env and fill in the keys.' Red
    exit 1
}
foreach ($p in @($port, $WebPort)) {
    if (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue) {
        Say "Port $p is already in use (a server may already be running). Stop it first, or open http://localhost:$WebPort." Red
        exit 1
    }
}

if (-not $SkipChecks) {
    Say '1/3 CPU load (real-time audio needs a quiet computer)...' Cyan
    $samples = 1..3 | ForEach-Object {
        (Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average
        Start-Sleep -Milliseconds 700
    }
    $cpu = [math]::Round(($samples | Measure-Object -Average).Average)
    if ($cpu -gt 50) {
        Say "   CPU is at $cpu%. Close heavy programs (Task Manager) before the demo, or calls will stutter." Yellow
    } else {
        Say "   CPU at $cpu% - fine." Green
    }

    Say '2/3 Free-tier quotas (ElevenLabs, Groq, Deepgram)...' Cyan
    if ((Invoke-Backend @('app.quota')) -ne 0) { Say '   A quota is low or a key failed (see above). The demo may not get through.' Yellow }
}

if ($RefreshSamples) {
    Say '3/3 Sample reservations (dated from today)...' Cyan
    if ((Invoke-Backend @('app.db.demo_data')) -ne 0) {
        Say '   Could not add the sample reservations (see above).' Red
        exit 1
    }
}

Say "Starting the backend on port $port and the frontend on port $WebPort (two new windows)..." Cyan
# No --reload: a file save mid-demo would restart the backend and drop the call.
$backendWindow = Start-Process powershell -PassThru -WorkingDirectory $backend -ArgumentList @(
    '-NoExit', '-Command', "uv run uvicorn app.main:app --port $port"
)
# A production build by default: pages load instantly and there's no dev badge on screen.
$web = if ($Dev) { "npm run dev -- -p $WebPort" }
       else { "npm run build; if (`$LASTEXITCODE -eq 0) { npm run start -- -p $WebPort }" }
$frontendWindow = Start-Process powershell -PassThru -WorkingDirectory $frontend -ArgumentList @(
    '-NoExit', '-Command', "`$env:BACKEND_URL='http://127.0.0.1:$port'; $web"
)
Say "   windows: backend PID $($backendWindow.Id), frontend PID $($frontendWindow.Id)"

$deadline = (Get-Date).AddSeconds(90)
$status = $null
while ((Get-Date) -lt $deadline -and -not $status) {
    Start-Sleep -Seconds 2
    try { $status = Invoke-RestMethod "http://127.0.0.1:$port/api/voice/status" -TimeoutSec 3 } catch { }
}
if (-not $status) { Say 'The backend did not start within 90 s; check its window.' Red; exit 1 }
if ($status.ready) { Say '   Voice: ready' Green } else { Say "   Voice: NOT ready - $($status.detail)" Red }
if ($status.bookings_online) { Say '   Bookings: online (database connected)' Green }
else { Say '   Bookings: OFFLINE - check DATABASE_URL and the backend window' Red }

if (-not $Dev) { Say '   Building the frontend (about 1-2 minutes)...' }
$deadline = (Get-Date).AddSeconds(300)
$up = $false
while ((Get-Date) -lt $deadline -and -not $up) {
    try { Invoke-WebRequest "http://localhost:$WebPort/login" -UseBasicParsing -TimeoutSec 30 | Out-Null; $up = $true }
    catch { Start-Sleep -Seconds 2 }
}
if (-not $up) { Say 'The frontend did not answer within 5 minutes; check its window.' Red; exit 1 }

Say "Ready: http://localhost:$WebPort. Make one short warm-up call on /call before the client joins." Green
if (-not $NoBrowser) { Start-Process "http://localhost:$WebPort" }
