# Runs one live bot process per pair (live/pairs.py) and keeps them all in
# sync with GitHub automatically.
#
# Checks for new commits every 5 minutes; if the code changed, pulls it and
# restarts every pair's bot so they all run what was just pushed. Also
# restarts any single pair's bot on its own if it crashes, without touching
# the other 9. Set up once as a Windows Scheduled Task (see live/README.md)
# so it survives RDP disconnects and keeps running unattended.
#
# Run from the repo root this script's own path lives under - it uses
# $PSScriptRoot's parent, not a hardcoded path, so this still works if the
# clone ever moves.

$RepoPath = Split-Path -Parent $PSScriptRoot
Set-Location $RepoPath

$CheckIntervalSeconds = 300
$LogDir = Join-Path $RepoPath "live\logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

# Mirrors live/pairs.py's PAIRS list. Keep these in sync if a pair is
# added or removed there.
$Pairs = @("AUD_USD", "EUR_JPY", "EUR_USD", "GBP_JPY", "GBP_USD",
           "NZD_USD", "USD_CAD", "USD_CHF", "USD_JPY", "XAU_USD",
           "EUR_GBP", "EUR_CHF", "EUR_AUD", "EUR_CAD", "EUR_NZD",
           "GBP_CHF", "GBP_AUD", "GBP_CAD", "GBP_NZD", "AUD_JPY",
           "AUD_CAD", "AUD_CHF", "AUD_NZD", "NZD_JPY", "NZD_CAD",
           "NZD_CHF", "CAD_JPY", "CHF_JPY")

function Start-Bot {
    param([string]$Instrument)
    # Runs hidden (no console window pops up), but stdout/stderr are
    # redirected to files instead of vanishing, so what the bot is doing -
    # settings loaded, signals found, orders placed, errors - stays visible
    # via `Get-Content -Tail 20 -Wait` on these files.
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    Write-Output "$(Get-Date): starting live/run_live.py $Instrument (log: bot_${Instrument}_$stamp.log)"
    # "-u" forces Python's stdout/stderr to be unbuffered. Without it, Python
    # fully buffers output whenever it's not talking to a real console (e.g.
    # redirected to a file here), so print() calls sit invisible in a buffer
    # instead of reaching the log file in real time.
    Start-Process -FilePath "python" -ArgumentList "-u", "live\run_live.py", $Instrument -WorkingDirectory $RepoPath -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "bot_${Instrument}_$stamp.log") `
        -RedirectStandardError (Join-Path $LogDir "bot_${Instrument}_$stamp.err.log")
}

$BotProcesses = @{}
foreach ($pair in $Pairs) {
    $BotProcesses[$pair] = Start-Bot -Instrument $pair
}

while ($true) {
    Start-Sleep -Seconds $CheckIntervalSeconds

    git fetch origin main 2>$null
    $Local = git rev-parse HEAD
    $Remote = git rev-parse origin/main

    if ($Local -ne $Remote) {
        Write-Output "$(Get-Date): new commit detected ($Local -> $Remote), updating and restarting all pairs"
        git pull --ff-only origin main

        foreach ($pair in $Pairs) {
            if (-not $BotProcesses[$pair].HasExited) {
                Stop-Process -Id $BotProcesses[$pair].Id -Force
            }
        }
        Start-Sleep -Seconds 3
        foreach ($pair in $Pairs) {
            $BotProcesses[$pair] = Start-Bot -Instrument $pair
        }
    }
    else {
        foreach ($pair in $Pairs) {
            if ($BotProcesses[$pair].HasExited) {
                Write-Output "$(Get-Date): $pair bot process was not running, restarting"
                $BotProcesses[$pair] = Start-Bot -Instrument $pair
            }
        }
    }
}
