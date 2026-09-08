[CmdletBinding()]
param(
    [string]$Configuration = 'Debug',
    [string]$TargetFramework = 'net8.0-windows',
    [string]$MSBuildPath = ''
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$projectPath = Join-Path $root 'suomenvaylat.csproj'

function Resolve-MSBuildPath {
    param([string]$ExplicitPath)

    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        $resolved = Resolve-Path -LiteralPath $ExplicitPath -ErrorAction Stop
        if ((Split-Path -Leaf $resolved.Path) -ine 'MSBuild.exe') {
            throw "MSBuildPath must point to MSBuild.exe: $($resolved.Path)"
        }
        return $resolved.Path
    }

    $candidates = @()
    $vsWhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path -LiteralPath $vsWhere -PathType Leaf) {
        $installations = @(& $vsWhere -latest -products * -requires Microsoft.Component.MSBuild -property installationPath)
        foreach ($installation in $installations) {
            if (-not [string]::IsNullOrWhiteSpace($installation)) {
                $candidates += Join-Path $installation 'MSBuild\Current\Bin\MSBuild.exe'
                $candidates += Join-Path $installation 'MSBuild\Current\Bin\amd64\MSBuild.exe'
            }
        }
    }

    $candidates += @(
        (Join-Path ${env:ProgramFiles} 'Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe'),
        (Join-Path ${env:ProgramFiles} 'Microsoft Visual Studio\2022\Professional\MSBuild\Current\Bin\MSBuild.exe'),
        (Join-Path ${env:ProgramFiles} 'Microsoft Visual Studio\2022\Enterprise\MSBuild\Current\Bin\MSBuild.exe'),
        (Join-Path ${env:ProgramFiles} 'Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe'),
        (Join-Path ${env:ProgramFiles} 'Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe'),
        (Join-Path ${env:ProgramFiles} 'Microsoft Visual Studio\18\Professional\MSBuild\Current\Bin\MSBuild.exe'),
        (Join-Path ${env:ProgramFiles} 'Microsoft Visual Studio\18\Enterprise\MSBuild\Current\Bin\MSBuild.exe'),
        (Join-Path ${env:ProgramFiles} 'Microsoft Visual Studio\18\BuildTools\MSBuild\Current\Bin\MSBuild.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\18\BuildTools\MSBuild\Current\Bin\MSBuild.exe')
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw 'Full-framework MSBuild.exe was not found. Install Visual Studio MSBuild or pass -MSBuildPath explicitly. Do not use dotnet build for the ArcGIS Pro SDK targets used by this project.'
}

$msbuild = Resolve-MSBuildPath -ExplicitPath $MSBuildPath
Write-Output "Using MSBuild: $msbuild"

& $msbuild $projectPath '/t:Rebuild' "/p:Configuration=$Configuration" "/p:TargetFramework=$TargetFramework" '/nologo' '/v:minimal'
if ($LASTEXITCODE -ne 0) {
    throw "MSBuild failed with exit code $LASTEXITCODE. The package was not created."
}

$assemblyPath = Join-Path $root "bin\$Configuration\$TargetFramework\suomenvaylat.dll"
if (-not (Test-Path -LiteralPath $assemblyPath -PathType Leaf)) {
    throw "Rebuild completed but the expected assembly was not found: $assemblyPath"
}

& (Join-Path $root 'package-addin.ps1') -Configuration $Configuration -TargetFramework $TargetFramework -AssemblyPath $assemblyPath
if ($LASTEXITCODE -ne 0) {
    throw "Packaging failed with exit code $LASTEXITCODE."
}
