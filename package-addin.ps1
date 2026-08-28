[CmdletBinding()]
param(
    [string]$Configuration = 'Debug',
    [string]$TargetFramework = 'net8.0-windows',
    [string]$AssemblyPath = '',
    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem

$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$outputDirectory = Join-Path $root "bin\$Configuration\$TargetFramework"
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $packagePath = Join-Path $outputDirectory 'suomenvaylat.esriAddInX'
} elseif ([IO.Path]::IsPathRooted($OutputPath)) {
    $packagePath = $OutputPath
} else {
    $packagePath = Join-Path $root $OutputPath
}

function Resolve-AssemblyPath {
    param([string]$ExplicitPath)

    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        $resolved = Resolve-Path -LiteralPath $ExplicitPath -ErrorAction Stop
        return $resolved.Path
    }

    $candidates = @(
        (Join-Path $outputDirectory 'suomenvaylat.dll'),
        (Join-Path $root "bin\$Configuration\suomenvaylat.dll")
    )

    $assemblyCacheRoot = Join-Path $env:LOCALAPPDATA 'ESRI\ArcGISPro\AssemblyCache\{2aea3c93-c012-4ed4-b416-6b3a844e0204}'
    if (Test-Path -LiteralPath $assemblyCacheRoot) {
        $candidates += @(
            Get-ChildItem -LiteralPath $assemblyCacheRoot -Filter 'suomenvaylat.dll' -File -Recurse -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending |
                ForEach-Object { $_.FullName }
        )
    }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "Compiled suomenvaylat.dll was not found. This script does not run MSBuild. Build the C# add-in once, or pass -AssemblyPath to an existing suomenvaylat.dll."
}

$assemblySource = Resolve-AssemblyPath -ExplicitPath $AssemblyPath
$temporaryDirectory = Join-Path $env:TEMP ('suomenvaylat-addin-' + [guid]::NewGuid().ToString('N'))
$temporaryPackagePath = Join-Path $env:TEMP ('suomenvaylat-' + [guid]::NewGuid().ToString('N') + '.esriAddInX')

$packageFiles = @(
    @{ Relative = 'Config.daml'; Source = (Join-Path $root 'Config.daml') },
    @{ Relative = 'suomenvaylat.dll'; Source = $assemblySource },
    @{ Relative = 'Images\AddInDesktop16.png'; Source = (Join-Path $root 'Images\AddInDesktop16.png') },
    @{ Relative = 'Images\AddInDesktop32.png'; Source = (Join-Path $root 'Images\AddInDesktop32.png') },
    @{ Relative = 'Images\Suomenvaylat16.png'; Source = (Join-Path $root 'Images\Suomenvaylat16.png') },
    @{ Relative = 'Images\Suomenvaylat32.png'; Source = (Join-Path $root 'Images\Suomenvaylat32.png') },
    @{ Relative = 'DarkImages\AddInDesktop16.png'; Source = (Join-Path $root 'DarkImages\AddInDesktop16.png') },
    @{ Relative = 'DarkImages\AddInDesktop32.png'; Source = (Join-Path $root 'DarkImages\AddInDesktop32.png') },
    @{ Relative = 'Toolboxes\VaylaWFSDownloader.pyt'; Source = (Join-Path $root 'Toolboxes\VaylaWFSDownloader.pyt') },
    @{ Relative = 'Toolboxes\Resources\credentials.wmts'; Source = (Join-Path $root 'Toolboxes\Resources\credentials.wmts') },
    @{ Relative = 'Toolboxes\Resources\hallinnolliset_aluejaot.gpkg'; Source = (Join-Path $root 'Toolboxes\Resources\hallinnolliset_aluejaot.gpkg') }
)

try {
    foreach ($item in $packageFiles) {
        if (-not (Test-Path -LiteralPath $item.Source -PathType Leaf)) {
            throw "Package input was not found: $($item.Source)"
        }
    }

    [xml]$config = Get-Content -LiteralPath (Join-Path $root 'Config.daml') -Raw
    if ($config.ArcGIS.defaultAssembly -ne 'suomenvaylat.dll') {
        throw "Config.daml defaultAssembly must be suomenvaylat.dll."
    }

    New-Item -ItemType Directory -Path $temporaryDirectory -Force | Out-Null
    foreach ($item in $packageFiles) {
        $relativePath = $item.Relative -replace '/', '\'
        $destination = Join-Path $temporaryDirectory $relativePath
        $destinationDirectory = Split-Path -Parent $destination
        New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
        Copy-Item -LiteralPath $item.Source -Destination $destination -Force
    }

    $packageParent = Split-Path -Parent $packagePath
    New-Item -ItemType Directory -Path $packageParent -Force | Out-Null
    [IO.Compression.ZipFile]::CreateFromDirectory(
        $temporaryDirectory,
        $temporaryPackagePath,
        [IO.Compression.CompressionLevel]::Optimal,
        $false
    )

    $zip = [IO.Compression.ZipFile]::OpenRead($temporaryPackagePath)
    try {
        $entryNames = @($zip.Entries | ForEach-Object { $_.FullName.Replace('/', '\') })
    }
    finally {
        $zip.Dispose()
    }

    foreach ($item in $packageFiles) {
        if ($entryNames -notcontains $item.Relative) {
            throw "Package validation failed: $($item.Relative) is missing."
        }
    }

    if (Test-Path -LiteralPath $packagePath) {
        Remove-Item -LiteralPath $packagePath -Force
    }
    Move-Item -LiteralPath $temporaryPackagePath -Destination $packagePath -Force
    $temporaryPackagePath = $null

    $packageInfo = Get-Item -LiteralPath $packagePath
    Write-Output "Created: $($packageInfo.FullName)"
    Write-Output "Size: $($packageInfo.Length) bytes"
    Write-Output "Entries: $($entryNames.Count)"
    Write-Output "Assembly: $assemblySource"
}
finally {
    if (Test-Path -LiteralPath $temporaryDirectory) {
        Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
    }
    if ($temporaryPackagePath -and (Test-Path -LiteralPath $temporaryPackagePath)) {
        Remove-Item -LiteralPath $temporaryPackagePath -Force
    }
}
