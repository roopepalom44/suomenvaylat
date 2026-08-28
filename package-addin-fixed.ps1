[CmdletBinding()]
param(
    [string]$Configuration = 'Debug',
    [string]$TargetFramework = 'net8.0-windows'
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$root = (Resolve-Path $PSScriptRoot).Path
$output = Join-Path $root "bin\$Configuration\$TargetFramework"
$stage = Join-Path $env:TEMP ('suomenvaylat-stage-' + [guid]::NewGuid().ToString('N'))
$zip = Join-Path $env:TEMP ('suomenvaylat-' + [guid]::NewGuid().ToString('N') + '.zip')
$package = Join-Path $output 'suomenvaylat.esriAddInX'

$files = @(
    @{ Relative = 'Config.daml'; Source = (Join-Path $root 'Config.daml') },
    @{ Relative = 'Images\AddInDesktop16.png'; Source = (Join-Path $root 'Images\AddInDesktop16.png') },
    @{ Relative = 'Images\AddInDesktop32.png'; Source = (Join-Path $root 'Images\AddInDesktop32.png') },
    @{ Relative = 'Images\Suomenvaylat16.png'; Source = (Join-Path $root 'Images\Suomenvaylat16.png') },
    @{ Relative = 'Images\Suomenvaylat32.png'; Source = (Join-Path $root 'Images\Suomenvaylat32.png') },
    @{ Relative = 'DarkImages\AddInDesktop16.png'; Source = (Join-Path $root 'DarkImages\AddInDesktop16.png') },
    @{ Relative = 'DarkImages\AddInDesktop32.png'; Source = (Join-Path $root 'DarkImages\AddInDesktop32.png') },
    @{ Relative = 'Install\suomenvaylat.dll'; Source = (Join-Path $output 'suomenvaylat.dll') },
    @{ Relative = 'Install\suomenvaylat.pdb'; Source = (Join-Path $output 'suomenvaylat.pdb') },
    @{ Relative = 'Install\suomenvaylat.deps.json'; Source = (Join-Path $output 'suomenvaylat.deps.json') },
    @{ Relative = 'Install\OpenSuomenvaylatToolButton.cs'; Source = (Join-Path $root 'OpenSuomenvaylatToolButton.cs') },
    @{ Relative = 'Install\Toolboxes\VaylaWFSDownloader.pyt'; Source = (Join-Path $root 'Toolboxes\VaylaWFSDownloader.pyt') },
    @{ Relative = 'Install\Toolboxes\Resources\credentials.wmts'; Source = (Join-Path $root 'Toolboxes\Resources\credentials.wmts') },
    @{ Relative = 'Install\Toolboxes\Resources\hallinnolliset_aluejaot.gpkg'; Source = (Join-Path $root 'Toolboxes\Resources\hallinnolliset_aluejaot.gpkg') }
)

try {
    foreach ($file in $files) {
        if (-not (Test-Path -LiteralPath $file.Source -PathType Leaf)) {
            throw "Missing package input: $($file.Source)"
        }
        $destination = Join-Path $stage $file.Relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
        Copy-Item -LiteralPath $file.Source -Destination $destination
    }

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $package) | Out-Null
    [IO.Compression.ZipFile]::CreateFromDirectory($stage, $zip, [IO.Compression.CompressionLevel]::Optimal, $false)
    $archive = [IO.Compression.ZipFile]::OpenRead($zip)
    try {
        $entries = @($archive.Entries | ForEach-Object { $_.FullName.Replace('/', '\') })
    }
    finally {
        $archive.Dispose()
    }

    foreach ($file in $files) {
        if ($entries -notcontains $file.Relative) {
            throw "Package validation failed: $($file.Relative)"
        }
    }
    if ($entries -contains 'suomenvaylat.dll' -or $entries -contains 'Toolboxes\VaylaWFSDownloader.pyt') {
        throw 'Package validation failed: runtime files must be under Install\.'
    }

    Move-Item -LiteralPath $zip -Destination $package -Force
    $zip = $null
    Write-Output "Created: $package"
    Write-Output 'Package layout: OK'
}
finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
    if ($zip -and (Test-Path -LiteralPath $zip)) { Remove-Item -LiteralPath $zip -Force }
}
