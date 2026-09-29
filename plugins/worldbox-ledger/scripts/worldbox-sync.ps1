param(
    [ValidateSet('Start','Status','Stop')][string]$Action = 'Status',
    [string]$Profile = (Join-Path $env:LOCALAPPDATA 'WorldBoxLedger\config.json')
)
$ErrorActionPreference = 'Stop'
$worker = Join-Path $PSScriptRoot 'worldbox_sync.py'
$python = (Get-Command python -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath $Profile -PathType Leaf)) { throw 'Configure the local WorldBox profile first.' }
$settings = Get-Content -LiteralPath $Profile -Raw -Encoding UTF8 | ConvertFrom-Json
# Let the Python path guard validate the destination before creating any log file.
$statusText = & $python -B $worker status --profile $Profile
if ($LASTEXITCODE -ne 0) { throw 'Profile validation failed.' }
$status = $statusText | ConvertFrom-Json
$running = $false
if ($status.status -eq 'running' -and $status.pid) {
    $elapsed = ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($status.heartbeat_utc)).TotalSeconds
    $running = ($elapsed -lt 30 -and $elapsed -ge 0 -and $null -ne (Get-Process -Id $status.pid -ErrorAction SilentlyContinue))
}
if ($Action -eq 'Stop') {
    & $python -B $worker stop --profile $Profile
    exit $LASTEXITCODE
}
if ($Action -eq 'Start' -and -not $running) {
    $control = Join-Path $settings.output_root '.sync'
    $arguments = '-B "' + $worker + '" run --profile "' + $Profile + '"'
    $started = Start-Process -FilePath $python -ArgumentList $arguments -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $control 'worker.stdout.log') `
        -RedirectStandardError (Join-Path $control 'worker.stderr.log')
    Write-Output ('Started local save synchronization, PID ' + $started.Id + '. Check Status after the first stable read.')
} else {
    [pscustomobject]@{ Running = $running; LastHeartbeat = $status.heartbeat_utc; PID = $status.pid; Slots = $status.slot_count; Errors = $status.errors } | ConvertTo-Json -Depth 6
}
