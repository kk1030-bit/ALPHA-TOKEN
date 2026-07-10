$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = (Get-Command python -ErrorAction Stop).Source
$logDir = Join-Path $root "logs"
$stdout = Join-Path $logDir "bot.stdout.log"
$stderr = Join-Path $logDir "bot.stderr.log"
$botPath = Join-Path $root "bot.py"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$existing = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like "python*" -and $_.CommandLine -like "*$botPath*"
}
if ($existing) {
    exit 0
}

Set-Location -LiteralPath $root
Start-Process `
    -FilePath $python `
    -ArgumentList "`"$botPath`"" `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr | Out-Null
