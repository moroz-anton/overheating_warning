$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $Python)) {
        throw 'Create .venv and install .[build] first; see README.md.'
    }
    & $Python tools\fetch_lhm.py
    if ($LASTEXITCODE -ne 0) { throw 'Could not fetch LibreHardwareMonitorLib.' }
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name OverheatingWarning --hidden-import win32timezone --hidden-import clr --collect-all pythonnet --collect-all clr_loader --add-data 'vendor\lhm;lhm' run.py
    if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
    Write-Host "Built $PSScriptRoot\dist\OverheatingWarning.exe"
}
finally {
    Pop-Location
}
