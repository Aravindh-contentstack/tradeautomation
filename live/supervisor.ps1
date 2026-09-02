# Runs ONE live bot process covering every pair in live/pairs.py (see
# live/run_live.py's own docstring for why it's a single process, not one
# per pair - short version: a small VPS can't hold 27+ separate Python
# interpreters in memory at once), and keeps it in sync with GitHub
# automatically.
#
# Checks for new commits every 5 minutes; if the code changed, pulls it and
# restarts the bot so it runs what was just pushed. Also restarts the bot
# if it crashes outright. Set up once as a Windows Scheduled Task (see
# live/README.md) so it survives RDP disconnects and keeps running
# unattended.
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
    # via `Get-Content -Tail 20 -Wait` on these files. No instrument list is
    # passed, so run_live.py defaults to every pair in live/pairs.py - that
    # file is the single source of truth for which pairs are live, not a
    # second list duplicated here.
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    Write-Output "$(Get-Date): starting live/run_live.py (all pairs) (log: bot_$stamp.log)"

    # Nothing ever deleted these before - every restart (crash, git-triggered,
    # or manual) minted a fresh pair of files and left them forever, which is
    # what filled the VPS disk. Keep only the newest 10 of each before adding
    # one more. "bot_*.log" alone would also match "bot_..._....err.log" (the
    # * covers ".err" too), so the .err.log files are excluded explicitly -
    # otherwise the two Remove-Item passes below would rank a mixed pool of
    # both file types instead of 10 of each kind.
    Get-ChildItem -Path $LogDir -Filter "bot_*.log" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notlike "*.err.log" } |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip 10 |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Path $LogDir -Filter "bot_*.err.log" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip 10 |
        Remove-Item -Force -ErrorAction SilentlyContinue
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
