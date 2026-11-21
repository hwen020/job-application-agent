param([switch]$Setup)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Setup) {
    python -m venv .venv
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    & .\.venv\Scripts\python.exe -m playwright install chromium
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
}
$interpreter = if (Test-Path .\.venv\Scripts\python.exe) { '.\.venv\Scripts\python.exe' } else { 'python' }
& $interpreter -m app.server
exit $LASTEXITCODE
