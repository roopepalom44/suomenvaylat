[CmdletBinding()]
param(
    [string]$PackagePath = '.\bin\Debug\net8.0-windows\suomenvaylat.esriAddInX'
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem

if (-not (Test-Path -LiteralPath $PackagePath -PathType Leaf)) {
    throw "Add-in package was not found: $PackagePath"
}

$packagePath = (Resolve-Path -LiteralPath $PackagePath).Path
$temporaryDirectory = Join-Path $env:TEMP ('suomenvaylat-diagnose-' + [guid]::NewGuid().ToString('N'))

try {
    New-Item -ItemType Directory -Path $temporaryDirectory -Force | Out-Null
    [IO.Compression.ZipFile]::ExtractToDirectory($packagePath, $temporaryDirectory)

    $configPath = Join-Path $temporaryDirectory 'Config.daml'
    $assemblyPath = Join-Path $temporaryDirectory 'suomenvaylat.dll'
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw 'Config.daml is missing from the package.'
    }
    if (-not (Test-Path -LiteralPath $assemblyPath -PathType Leaf)) {
        throw 'suomenvaylat.dll is missing from the package.'
    }

    [xml]$config = Get-Content -LiteralPath $configPath -Raw
    $module = $config.ArcGIS.modules.insertModule
    $button = $module.controls.button | Where-Object { $_.id -eq 'suomenvaylat_SuomenvaylatButton' }
    $defaultAssembly = $config.ArcGIS.defaultAssembly
    $defaultNamespace = $config.ArcGIS.defaultNamespace
    $moduleType = $module.className
    $buttonType = $button.className

    Write-Output "Package: $packagePath"
    Write-Output "Add-in id: $($config.ArcGIS.AddInInfo.id)"
    Write-Output "Add-in version: $($config.ArcGIS.AddInInfo.version)"
    Write-Output "Config defaultAssembly: $defaultAssembly"
    Write-Output "Config defaultNamespace: $defaultNamespace"
    Write-Output "Module class: $moduleType"
    Write-Output "Button class: $buttonType"

    $assemblyName = [Reflection.AssemblyName]::GetAssemblyName($assemblyPath)
    Write-Output "Assembly identity: $($assemblyName.FullName)"
    Write-Output "Assembly bytes: $((Get-Item -LiteralPath $assemblyPath).Length)"

    $expectedTypes = @($moduleType, $buttonType)
    $assembly = $null
    try {
        $assembly = [Reflection.Assembly]::LoadFrom($assemblyPath)
        foreach ($expectedType in $expectedTypes) {
            $found = $assembly.GetType($expectedType, $false, $false)
            if ($found) {
                Write-Output "Type found: $expectedType"
            } else {
                Write-Output "TYPE MISSING: $expectedType"
            }
        }
        Write-Output 'Assembly load: succeeded'
    }
    catch [Reflection.ReflectionTypeLoadException] {
        Write-Output 'Assembly load: ReflectionTypeLoadException'
        foreach ($loaderException in $_.Exception.LoaderExceptions) {
            Write-Output "Loader error: $($loaderException.Message)"
        }
    }
    catch {
        Write-Output "Assembly load: FAILED - $($_.Exception.GetType().FullName): $($_.Exception.Message)"
    }

    $zip = [IO.Compression.ZipFile]::OpenRead($packagePath)
    try {
        $entryNames = @($zip.Entries | ForEach-Object { $_.FullName.Replace('/', '\') })
    }
    finally {
        $zip.Dispose()
    }
    Write-Output "Package entries: $($entryNames.Count)"
    Write-Output "Required entries present: $([bool]($entryNames -contains 'Config.daml' -and $entryNames -contains 'suomenvaylat.dll' -and $entryNames -contains 'Toolboxes\VaylaWFSDownloader.pyt'))"

    $addinId = $config.ArcGIS.AddInInfo.id
    $cachePath = Join-Path $env:LOCALAPPDATA "ESRI\ArcGISPro\AssemblyCache\$addinId"
    if (Test-Path -LiteralPath $cachePath) {
        Write-Output "AssemblyCache: $cachePath"
        Get-ChildItem -LiteralPath $cachePath -Recurse -File -ErrorAction SilentlyContinue |
            Select-Object FullName, Length, LastWriteTime |
            Format-Table -AutoSize | Out-String | Write-Output
    } else {
        Write-Output "AssemblyCache: not found at $cachePath"
    }
}
finally {
    if (Test-Path -LiteralPath $temporaryDirectory) {
        Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
    }
}