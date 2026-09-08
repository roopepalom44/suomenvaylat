[CmdletBinding()]
param(
    [string]$Configuration = 'Debug',
    [string]$TargetFramework = 'net8.0-windows',
    [string]$AssemblyPath = '',
    [string]$OutputPath = ''
)

# Backward-compatible alias. Keep one implementation so the old "fixed"
# command cannot drift away from the validated packaging rules.
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'package-addin.ps1') `
    -Configuration $Configuration `
    -TargetFramework $TargetFramework `
    -AssemblyPath $AssemblyPath `
    -OutputPath $OutputPath
