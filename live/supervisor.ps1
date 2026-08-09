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

function Start-Bot {
    Write-Output "$(Get-Date): starting live/run_live.py"
    Start-Process -FilePath "python" -ArgumentList "live\run_live.py" -WorkingDirectory $RepoPath -PassThru -WindowStyle Hidden
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
