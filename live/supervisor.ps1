# Runs the live trading bot and keeps it in sync with GitHub automatically.
#
# Checks for new commits every 5 minutes; if the code changed, pulls it and
# restarts the bot so it always runs what was just pushed. Also restarts the
# bot on its own if it crashes for any other reason. Set up once as a
# Windows Scheduled Task (see live/README.md) so it survives RDP
# disconnects and keeps running unattended.
#
# Run from the repo root this script's own path lives under - it uses
# $PSScriptRoot's parent, not a hardcoded path, so this still works if the
# clone ever moves.

$RepoPath = Split-Path -Parent $PSScriptRoot
Set-Location $RepoPath

$CheckIntervalSeconds = 300
$LogDir = Join-Path $RepoPath "live\logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Start-Bot {
    # Runs hidden (no console window pops up), but stdout/stderr are
    # redirected to files instead of vanishing, so what the bot is doing -
    # settings loaded, signals found, orders placed, errors - stays visible
    # via `Get-Content -Tail 20 -Wait` on these files.
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    Write-Output "$(Get-Date): starting live/run_live.py (log: bot_$stamp.log)"
    # "-u" forces Python's stdout/stderr to be unbuffered. Without it, Python
    # fully buffers output whenever it's not talking to a real console (e.g.
    # redirected to a file here), so print() calls sit invisible in a buffer
    # instead of reaching the log file in real time.
    Start-Process -FilePath "python" -ArgumentList "-u", "live\run_live.py" -WorkingDirectory $RepoPath -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "bot_$stamp.log") `
        -RedirectStandardError (Join-Path $LogDir "bot_$stamp.err.log")
}

$BotProcess = Start-Bot

while ($true) {
    Start-Sleep -Seconds $CheckIntervalSeconds

    git fetch origin main 2>$null
    $Local = git rev-parse HEAD
    $Remote = git rev-parse origin/main

    if ($Local -ne $Remote) {
        Write-Output "$(Get-Date): new commit detected ($Local -> $Remote), updating and restarting"
        git pull --ff-only origin main

        if (-not $BotProcess.HasExited) {
            Stop-Process -Id $BotProcess.Id -Force
        }
        Start-Sleep -Seconds 3
        $BotProcess = Start-Bot
    }
    elseif ($BotProcess.HasExited) {
        Write-Output "$(Get-Date): bot process was not running, restarting"
        $BotProcess = Start-Bot
    }
}
