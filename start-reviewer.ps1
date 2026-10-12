param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
if ($Port -ne 8765) { throw 'This release uses port 8765 so browser write-origin checks remain consistent.' }
$projectRoot = $PSScriptRoot
$reviewerPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $reviewerPython)) { throw 'Project Python environment is missing. Create .venv, then run setup-reviewer.ps1.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'reviewer/frontend/dist/index.html'))) { throw 'Frontend is not built. Run setup-reviewer.ps1 first.' }
& $reviewerPython -c "import fastapi, uvicorn, multipart, nbtlib"
if ($LASTEXITCODE -ne 0) { throw 'Reviewer dependencies are missing. Run setup-reviewer.ps1 first.' }
Write-Host 'Open http://127.0.0.1:8765 in your browser. Press Ctrl+C here to stop.'
& $reviewerPython -m uvicorn reviewer.app:app --app-dir $projectRoot --host 127.0.0.1 --port $Port --no-access-log
if ($LASTEXITCODE -ne 0) { throw 'Reviewer stopped with an error. If port 8765 is busy, stop the other reviewer instance.' }
