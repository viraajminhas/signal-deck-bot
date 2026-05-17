# SignalDeckBot - Windows Task Scheduler installer
#
# Registers a recurring task that runs bot.py every 5 minutes on weekdays,
# from 6:00 AM to 8:00 PM local time. The bot itself gates on Alpaca's
# market-open clock, so wide local hours just ensure we cover ET RTH no
# matter your timezone, plus give crypto a chance to trade.
#
# Run from an ELEVATED PowerShell prompt:
#   PowerShell -ExecutionPolicy Bypass -File .\setup_scheduler.ps1
#
# To verify after install:   schtasks /Query /TN SignalDeckBot /V /FO LIST
# To remove:                 schtasks /Delete /TN SignalDeckBot /F

$ErrorActionPreference = 'Stop'

$TaskName = 'SignalDeckBot'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = (Get-Command python).Source
$Bot = Join-Path $ScriptDir 'bot.py'

if (-not (Test-Path $Bot)) {
    throw "bot.py not found at $Bot"
}

# Build the action command; schtasks needs the whole thing as a single string
$Cmd = "`"$Python`" `"$Bot`""

# Remove any prior registration (no-op if it doesn't exist)
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Removing existing $TaskName task..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Create: weekdays Mon-Fri, start 06:00 local, repeat every 5 min for 14 hours
& schtasks /Create `
    /SC WEEKLY `
    /D MON,TUE,WED,THU,FRI `
    /TN $TaskName `
    /TR $Cmd `
    /ST 06:00 `
    /RI 5 `
    /DU 14:00 `
    /F

if ($LASTEXITCODE -ne 0) {
    throw "schtasks /Create failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "=== SignalDeckBot scheduled ===" -ForegroundColor Green
Write-Host "Working dir: $ScriptDir"
Write-Host "Python:      $Python"
Write-Host "Bot:         $Bot"
Write-Host "Cadence:     every 5 min, Mon-Fri, 6:00 AM to 8:00 PM local time"
Write-Host ""
Write-Host "Verify:  schtasks /Query /TN $TaskName /V /FO LIST"
Write-Host "Logs:    Get-Content '$ScriptDir\bot.log' -Tail 50 -Wait"
Write-Host "Remove:  schtasks /Delete /TN $TaskName /F"
Write-Host ""
Write-Host "REMINDER: DRY_RUN=1 in .env - bot will log decisions but place no orders." -ForegroundColor Yellow
Write-Host "          Set DRY_RUN=0 in .env when ready for paper-trade execution." -ForegroundColor Yellow
