# Start the server in this terminal. Ctrl+C stops it without deleting your data.
# PSScriptRoot anchors paths to this file, regardless of the current directory.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$inventoryExit = 1
try {
    $inventoryPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $inventoryPython)) {
        $inventoryPython = Join-Path $PSScriptRoot '..\..\work\.venv\Scripts\python.exe'
    }
    if (-not (Test-Path -LiteralPath $inventoryPython)) {
        throw 'Python setup is needed. Follow Windows setup in README.md.'
    }
    # Both shortcuts must honor the same account/version/HTTPS configuration.
    # Starting Uvicorn here directly would silently ignore enabled phone access.
    & $inventoryPython (Join-Path $PSScriptRoot 'launch.py')
    $inventoryExit = $LASTEXITCODE
} catch {
    Write-Host "Inventory could not start: $($_.Exception.Message)"
}
Write-Host 'Launcher finished. Keep any original inventory server window open.'
Read-Host 'Press Enter to close this window'
exit $inventoryExit
