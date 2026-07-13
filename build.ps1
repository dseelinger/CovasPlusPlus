# build.ps1 - build COVAS++ into a one-folder app (I5) and, optionally, a Setup.exe (I6).
# Run from the repo root:
#   .\build.ps1              # clean PyInstaller freeze from covas.spec
#   .\build.ps1 -SelfTest    # ...then run the frozen exe's headless import self-test
#   .\build.ps1 -Installer   # ...then compile covas.iss into dist\installer\COVAS++ Setup.exe
#
# Build deps come from requirements-build.txt (PyInstaller + pywebview); the installer step also
# needs Inno Setup 6 (ISCC.exe): https://jrsoftware.org/isdl.php
#   .venv\Scripts\python.exe -m pip install -r requirements-build.txt
param([switch]$SelfTest, [switch]$Installer)

$ErrorActionPreference = "Stop"
$py = ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) { throw "venv python not found at $py - run from the repo root." }

# --- Version: single source of truth is covas/__version__.py (I2). ---
$verLine = Select-String -Path "covas\__version__.py" -Pattern '__version__\s*=\s*"([^"]+)"'
if (-not $verLine) { throw "could not read __version__ from covas\__version__.py" }
$AppVersion = $verLine.Matches[0].Groups[1].Value
Write-Host "== COVAS++ version $AppVersion ==" -ForegroundColor Cyan

# --- Freeze (I5). ---
Write-Host "== Freezing COVAS++ (one-folder) ==" -ForegroundColor Cyan
& $py -m PyInstaller --noconfirm --clean covas.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }

$exe = "dist\COVAS++\COVAS++.exe"
if (-not (Test-Path $exe)) { throw "expected $exe was not produced" }

$sizeMB = [math]::Round((Get-ChildItem dist\COVAS++ -Recurse | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "== Freeze complete: $exe  (folder $sizeMB MB) ==" -ForegroundColor Green

if ($SelfTest) {
    Write-Host "== Running frozen self-test (headless native-lib import check) ==" -ForegroundColor Cyan
    & $exe --selftest
    if ($LASTEXITCODE -ne 0) { throw "frozen self-test FAILED (exit $LASTEXITCODE)" }
    Write-Host "== Self-test passed ==" -ForegroundColor Green
}

# --- Installer (I6): compile covas.iss with ISCC, stamping the version. ---
if ($Installer) {
    $iscc = (Get-Command ISCC -ErrorAction SilentlyContinue).Source
    if (-not $iscc) {
        foreach ($p in @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
                         "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                         "$env:ProgramFiles\Inno Setup 6\ISCC.exe")) {
            if (Test-Path $p) { $iscc = $p; break }
        }
    }
    if (-not $iscc) {
        throw "ISCC.exe (Inno Setup 6) not found. Install it from https://jrsoftware.org/isdl.php"
    }

    Write-Host "== Compiling installer (ISCC) ==" -ForegroundColor Cyan
    & $iscc "/DAppVersion=$AppVersion" covas.iss
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit $LASTEXITCODE)" }

    $setup = "dist\installer\COVAS++ Setup.exe"
    if (-not (Test-Path $setup)) { throw "expected $setup was not produced" }
    $setupMB = [math]::Round((Get-Item $setup).Length / 1MB, 1)
    Write-Host "== Installer complete: $setup  ($setupMB MB) ==" -ForegroundColor Green
}
