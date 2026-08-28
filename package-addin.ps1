[CmdletBinding()]
param(
    [string]$Configuration = 'Debug',
    [string]$TargetFramework = 'net8.0-windows'
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem

$root = (Resolve-Path (Join-Path $PSScriptRoot '.')).Path
$outputDirectory = Join-Path $root "bin\$Configuration\$TargetFramework"
$packagePath = Join-Path $outputDirectory 'suomenvaylat.esriAddInX'
$temporaryDirectory = Join-Path $env:TEMP ('suomenvaylat-addin-' + [guid]::NewGuid().ToString('N'))
$temporaryPackagePath = Join-Path $env:TEMP ('suomenvaylat-' + [guid]::NewGuid().ToString('N') + '.esriAddInX')

try {
    if (-not (Test-Path -LiteralPath $packagePath)) {
        throw "Existing add-in package was not found: $packagePath"
    }

    New-Item -ItemType Directory -Path $temporaryDirectory -Force | Out-Null
    [IO.Compression.ZipFile]::ExtractToDirectory($packagePath, $temporaryDirectory)

    $sourceFiles = @{
        'Config.daml' = Join-Path $root 'Config.daml'
        'VaylaWFSDownloader.pyt' = Join-Path $root 'Toolboxes\VaylaWFSDownloader.pyt'
    }

    foreach ($fileName in $sourceFiles.Keys) {
        $sourcePath = $sourceFiles[$fileName]
        if (-not (Test-Path -LiteralPath $sourcePath)) {
            throw "Source file was not found: $sourcePath"
        }

        $packageFile = Get-ChildItem -LiteralPath $temporaryDirectory -Filter $fileName -Recurse | Select-Object -First 1
        if (-not $packageFile) {
            throw "$fileName was not found inside the existing add-in package."
        }

        Copy-Item -LiteralPath $sourcePath -Destination $packageFile.FullName -Force
    }

    [IO.Compression.ZipFile]::CreateFromDirectory(
        $temporaryDirectory,
        $temporaryPackagePath,
        [IO.Compression.CompressionLevel]::Optimal,
        $false
    )

    $zip = [IO.Compression.ZipFile]::OpenRead($temporaryPackagePath)
    try {
        $entryNames = @($zip.Entries | ForEach-Object { $_.FullName })
    }
    finally {
        $zip.Dispose()
    }

    foreach ($requiredFile in @('Config.daml', 'VaylaWFSDownloader.pyt')) {
        if (-not ($entryNames | Where-Object { $_ -like "*$requiredFile" })) {
            throw "Validation failed: $requiredFile is missing from the new package."
        }
    }

    Move-Item -LiteralPath $temporaryPackagePath -Destination $packagePath -Force
    $temporaryPackagePath = $null

    $packageInfo = Get-Item -LiteralPath $packagePath
    Write-Output "Created: $($packageInfo.FullName)"
    Write-Output "Size: $($packageInfo.Length) bytes"
    Write-Output "Entries: $($entryNames.Count)"
}
finally {
    if (Test-Path -LiteralPath $temporaryDirectory) {
        Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
    }
    if ($temporaryPackagePath -and (Test-Path -LiteralPath $temporaryPackagePath)) {
        Remove-Item -LiteralPath $temporaryPackagePath -Force
    }
}
