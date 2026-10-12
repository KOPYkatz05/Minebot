$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$reviewerPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $reviewerPython)) { throw 'Create a project .venv with Python 3.12 before running setup.' }
$packageManager = Get-Command pnpm -ErrorAction SilentlyContinue
$packageManagerPath = if ($packageManager) { $packageManager.Source } else {
    Join-Path ([Environment]::GetFolderPath('UserProfile')) '.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/pnpm.cmd'
}
if (-not (Test-Path -LiteralPath $packageManagerPath)) { throw 'Install Node.js and pnpm, or run setup from the Codex environment where they are available.' }
& $reviewerPython -m pip install -r (Join-Path $projectRoot 'requirements.txt') -r (Join-Path $projectRoot 'requirements-reviewer.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
Push-Location (Join-Path $projectRoot 'reviewer/frontend')
try {
    & $packageManagerPath install --frozen-lockfile --ignore-scripts
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    & $packageManagerPath run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
Write-Host 'Setup complete. Run start-reviewer.ps1, then open http://127.0.0.1:8765.'
