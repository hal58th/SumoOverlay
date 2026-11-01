param(
    [ValidateSet("both", "ocr", "no-ocr")]
    [string]$Variant = "both",
    [string]$PythonPath
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    Join-Path $ProjectRoot ".venv-build\Scripts\python.exe"
}
elseif ([System.IO.Path]::IsPathRooted($PythonPath)) {
    $PythonPath
}
else {
    Join-Path $ProjectRoot $PythonPath
}
$DistPath = Join-Path $ProjectRoot "dist"

function Build-Release {
    param(
        [string]$Name,
        [string]$EntryPoint,
        [bool]$IncludeOcr
    )

    $ReleaseDirectory = Join-Path $DistPath $Name
    $ArchivePath = Join-Path $DistPath "$Name.zip"
    $WorkPath = Join-Path $ProjectRoot "build\pyinstaller-$Name"
    $SpecPath = Join-Path $ProjectRoot "build\pyinstaller-spec-$Name"

    if (Test-Path -LiteralPath $ReleaseDirectory) {
        Remove-Item -LiteralPath $ReleaseDirectory -Recurse -Force
    }
    if (Test-Path -LiteralPath $ArchivePath) {
        Remove-Item -LiteralPath $ArchivePath -Force
    }

    $Arguments = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--name", $Name,
        "--distpath", $DistPath,
        "--workpath", $WorkPath,
        "--specpath", $SpecPath,
        "--paths", $ProjectRoot,
        "--add-data", ((Join-Path $ProjectRoot "templates") + ";templates"),
        "--add-data", ((Join-Path $ProjectRoot "static") + ";static"),
        "--runtime-hook", (Join-Path $PSScriptRoot "pyi_rth_dll_dirs.py"),
        "--hidden-import", "engineio.async_drivers.threading"
    )

    if ($IncludeOcr) {
        $Arguments += @(
            "--collect-all", "easyocr",
            "--collect-all", "torch",
            "--collect-all", "torchvision"
        )
    }
    else {
        $Arguments += @(
            "--exclude-module", "easyocr",
            "--exclude-module", "torch",
            "--exclude-module", "torchvision",
            "--exclude-module", "mss",
            "--exclude-module", "numpy"
        )
    }

    $Arguments += $EntryPoint
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed while building $Name."
    }

    Copy-Item -LiteralPath "LICENSE", "config.vlc.yaml",
        "ocr_guide_tight_capture.png" `
        -Destination $ReleaseDirectory
    Copy-Item -LiteralPath "custom_templates" -Destination $ReleaseDirectory -Recurse

    if ($IncludeOcr) {
        Copy-Item -LiteralPath "config.yaml" -Destination $ReleaseDirectory
    }
    else {
        $ConfigureManualSettings = Join-Path $PSScriptRoot "configure_manual_config.py"
        & $Python $ConfigureManualSettings "config.yaml" (Join-Path $ReleaseDirectory "config.yaml")
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create the manual-mode configuration for $Name."
        }
    }

    Copy-Item -LiteralPath "README.md" `
        -Destination (Join-Path $ReleaseDirectory "README.md")
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README.md") `
        -Destination (Join-Path $ReleaseDirectory "BUILDING.md")
    Compress-Archive -Path (Join-Path $ReleaseDirectory "*") -DestinationPath $ArchivePath
    Write-Host "Created $ReleaseDirectory and $ArchivePath"
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Build Python not found at $Python. See packaging\README.md to create the build environment."
}

Push-Location $ProjectRoot
try {
    & $Python -m PyInstaller --version
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller is missing. Install the build requirements from packaging\README.md."
    }

    if ($Variant -in @("both", "ocr")) {
        & $Python -c "import easyocr, torch, torchvision"
        if ($LASTEXITCODE -ne 0) {
            throw "OCR dependencies are missing. Install the OCR build requirements from packaging\README.md."
        }
        Build-Release -Name "SumoOverlay" -EntryPoint "packaging\entry_ocr.py" -IncludeOcr $true
    }

    if ($Variant -in @("both", "no-ocr")) {
        Build-Release -Name "SumoOverlay-NoOCR" -EntryPoint "packaging\entry_manual.py" -IncludeOcr $false
    }
}
finally {
    Pop-Location
}
