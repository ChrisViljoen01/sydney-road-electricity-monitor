param(
    [switch]$SkipDependencyInstall,
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project Python environment not found at $Python"
}

Push-Location $Root
try {
    if (-not $SkipDependencyInstall) {
        & $Python -m pip install -r requirements.txt -r build-requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    }

    & $Python packaging\prepare_build.py
    if ($LASTEXITCODE -ne 0) { throw 'Installer resource preparation failed.' }

    & $Python -m PyInstaller --noconfirm --clean packaging\SydneyRoadElectricityMonitor.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller application build failed.' }

    if ($SkipInstaller) {
        Write-Host "Packaged application created in dist\Sydney Road Electricity Monitor"
        return
    }

    $InnoCandidates = @(
        @(
            (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
            (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
            (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
        ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    )
    if (-not $InnoCandidates) {
        throw 'Inno Setup 6 is required to create the installer EXE. Install it and run this script again.'
    }

    $InnoCompiler = $InnoCandidates[0]
    & $InnoCompiler packaging\SydneyRoadElectricityMonitor.iss
    if ($LASTEXITCODE -ne 0) { throw 'Windows installer compilation failed.' }
    Write-Host "Installer created in dist\installer"
}
finally {
    Pop-Location
}
