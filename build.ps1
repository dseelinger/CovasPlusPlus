# build.ps1 — freeze COVAS++ into a one-folder Windows app (I5). Run from the repo root:
#   .\build.ps1              # clean build from covas.spec
#   .\build.ps1 -SelfTest    # ...then run the frozen exe's headless import self-test
#
# Produces dist\COVAS++\COVAS++.exe. Build deps come from requirements-build.txt (PyInstaller +
# pywebview) — install them first:  .venv\Scripts\python.exe -m pip install -r requirements-build.txt
param([switch]$SelfTest)

$ErrorActionPreference = "Stop"
$py = ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) { throw "venv python not found at $py - run from the repo root." }

Write-Host "== Freezing COVAS++ (one-folder) ==" -ForegroundColor Cyan
& $py -m PyInstaller --noconfirm --clean covas.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }

$exe = "dist\COVAS++\COVAS++.exe"
if (-not (Test-Path $exe)) { throw "expected $exe was not produced" }

$sizeMB = [math]::Round((Get-ChildItem dist\COVAS++ -Recurse | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "== Build complete: $exe  (folder $sizeMB MB) ==" -ForegroundColor Green

if ($SelfTest) {
    Write-Host "== Running frozen self-test (headless native-lib import check) ==" -ForegroundColor Cyan
    & $exe --selftest
    if ($LASTEXITCODE -ne 0) { throw "frozen self-test FAILED (exit $LASTEXITCODE)" }
    Write-Host "== Self-test passed ==" -ForegroundColor Green
}
