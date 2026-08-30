$ErrorActionPreference = "Stop"

$projectDir = $PSScriptRoot
$venvPython = Join-Path $projectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Prepared project interpreter not found: $venvPython. Run uv sync first."
}

Set-Location -LiteralPath $projectDir
& $venvPython ".\main.py" --settings ".\settings.toml"
exit $LASTEXITCODE
