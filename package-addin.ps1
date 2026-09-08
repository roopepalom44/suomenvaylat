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

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "Compiled suomenvaylat.dll was not found in the build output. Rebuild the C# add-in with the ArcGIS Pro SDK, or pass the resulting DLL explicitly with -AssemblyPath. An AssemblyCache DLL is never selected automatically because it may be stale."
}

$assemblySource = Resolve-AssemblyPath -ExplicitPath $AssemblyPath
try {
    [Reflection.AssemblyName]::GetAssemblyName($assemblySource) | Out-Null
}
catch {
    throw "Assembly is not a valid managed .NET assembly: $assemblySource"
}

$assemblyName = [Reflection.AssemblyName]::GetAssemblyName($assemblySource)
if ($assemblyName.Name -ne 'suomenvaylat') {
    throw "Assembly name '$($assemblyName.Name)' does not match Config.daml defaultAssembly 'suomenvaylat.dll'."
}
Write-Output "Assembly identity: $($assemblyName.FullName)"
$assemblyDirectory = Split-Path -Parent $assemblySource

if ([string]::IsNullOrWhiteSpace($AssemblyPath)) {
    $assemblyItem = Get-Item -LiteralPath $assemblySource
    $latestCSharpSource = @(
        (Join-Path $root 'Module1.cs'),
        (Join-Path $root 'OpenSuomenvaylatToolButton.cs'),
        (Join-Path $root 'suomenvaylat.csproj')
    ) |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Get-Item |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if ($latestCSharpSource -and $assemblyItem.LastWriteTimeUtc -lt $latestCSharpSource.LastWriteTimeUtc) {
        throw "The build output DLL is older than the C# source ($($latestCSharpSource.Name)). Rebuild the add-in before packaging so ArcGIS Pro cannot receive an old button type."
    }
}

$configPath = Join-Path $root 'Config.daml'
[xml]$config = Get-Content -LiteralPath $configPath -Raw
if ($config.ArcGIS.defaultAssembly -ne 'suomenvaylat.dll') {
    throw "Config.daml defaultAssembly must be suomenvaylat.dll."
}
if ($config.ArcGIS.defaultNamespace -ne 'suomenvaylat') {
    throw "Config.daml defaultNamespace must be suomenvaylat."
}

$moduleClass = $config.ArcGIS.modules.insertModule.className
$buttonClass = ($config.ArcGIS.modules.insertModule.controls.button |
    Where-Object { $_.id -eq 'suomenvaylat_SuomenvaylatButton' }).className
$expectedTypes = @($moduleClass, $buttonClass) | ForEach-Object {
    if ($_ -like '*.*') { $_ } else { "$($config.ArcGIS.defaultNamespace).$_" }
}
if ($expectedTypes -contains '' -or $expectedTypes.Count -ne 2) {
    throw "Config.daml does not define both the module and button class names."
}

$typesValidated = $false
try {
    $assembly = [Reflection.Assembly]::Load([IO.File]::ReadAllBytes($assemblySource))
    try {
        $availableTypes = @($assembly.GetTypes())
        $missingTypes = @()
        foreach ($expectedType in $expectedTypes) {
            if (-not ($availableTypes | Where-Object { $_.FullName -eq $expectedType })) {
                $missingTypes += $expectedType
            }
        }
        if ($missingTypes.Count -gt 0) {
            throw "Assembly is missing expected CLR type(s): $($missingTypes -join ', ')"
        }
        $typesValidated = $true
    }
    catch [Reflection.ReflectionTypeLoadException] {
        $loaderMessages = @($_.Exception.LoaderExceptions | Where-Object { $_ } | ForEach-Object { $_.Message })
        if ($loaderMessages.Count -eq 0) {
            throw
        }
        Write-Warning "CLR type inspection was blocked by unresolved assembly dependencies. Falling back to metadata string checks."
        foreach ($loaderMessage in $loaderMessages) {
            Write-Warning "Loader error: $loaderMessage"
        }
    }
}
catch {
    if ($_.Exception.Message -like 'Assembly is missing expected CLR type(s):*') {
        throw
    }
    Write-Warning "CLR type inspection was unavailable in this PowerShell runtime ($($_.Exception.Message)). Falling back to metadata string checks."
}

if (-not $typesValidated) {
    $assemblyText = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($assemblySource))
    foreach ($requiredType in @('Module1', 'OpenSuomenvaylatToolButton')) {
        if (-not $assemblyText.Contains($requiredType)) {
            throw "Assembly does not contain the expected add-in type '$requiredType': $assemblySource. Build the current C# project or pass the correct DLL with -AssemblyPath."
        }
    }
}

$temporaryDirectory = Join-Path $env:TEMP ('suomenvaylat-addin-' + [guid]::NewGuid().ToString('N'))
$temporaryPackagePath = Join-Path $env:TEMP ('suomenvaylat-' + [guid]::NewGuid().ToString('N') + '.esriAddInX')

$packageFiles = @(
    @{ Relative = 'Config.daml'; Source = (Join-Path $root 'Config.daml'); Required = $true },
    @{ Relative = 'Images\AddInDesktop16.png'; Source = (Join-Path $root 'Images\AddInDesktop16.png'); Required = $true },
    @{ Relative = 'Images\AddInDesktop32.png'; Source = (Join-Path $root 'Images\AddInDesktop32.png'); Required = $true },
    @{ Relative = 'Images\Suomenvaylat16.png'; Source = (Join-Path $root 'Images\Suomenvaylat16.png'); Required = $true },
    @{ Relative = 'Images\Suomenvaylat32.png'; Source = (Join-Path $root 'Images\Suomenvaylat32.png'); Required = $true },
    @{ Relative = 'DarkImages\AddInDesktop16.png'; Source = (Join-Path $root 'DarkImages\AddInDesktop16.png'); Required = $true },
    @{ Relative = 'DarkImages\AddInDesktop32.png'; Source = (Join-Path $root 'DarkImages\AddInDesktop32.png'); Required = $true },
    @{ Relative = 'Install\suomenvaylat.dll'; Source = $assemblySource; Required = $true },
    @{ Relative = 'Install\suomenvaylat.pdb'; Source = (Join-Path $assemblyDirectory 'suomenvaylat.pdb'); Required = $false },
    @{ Relative = 'Install\suomenvaylat.deps.json'; Source = (Join-Path $assemblyDirectory 'suomenvaylat.deps.json'); Required = $false },
    @{ Relative = 'Install\suomenvaylat.runtimeconfig.json'; Source = (Join-Path $assemblyDirectory 'suomenvaylat.runtimeconfig.json'); Required = $false },
    @{ Relative = 'Install\Toolboxes\VaylaWFSDownloader.pyt'; Source = (Join-Path $root 'Toolboxes\VaylaWFSDownloader.pyt'); Required = $true },
    @{ Relative = 'Install\Toolboxes\Resources\credentials.wmts'; Source = (Join-Path $root 'Toolboxes\Resources\credentials.wmts'); Required = $true },
    @{ Relative = 'Install\Toolboxes\Resources\hallinnolliset_aluejaot.gpkg'; Source = (Join-Path $root 'Toolboxes\Resources\hallinnolliset_aluejaot.gpkg'); Required = $true }
)
$packageFiles = @($packageFiles | Where-Object {
    $_.Required -or (Test-Path -LiteralPath $_.Source -PathType Leaf)
})

try {
    foreach ($item in $packageFiles) {
        if (-not (Test-Path -LiteralPath $item.Source -PathType Leaf)) {
            throw "Package input was not found: $($item.Source)"
        }
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

    if ($entryNames -contains 'suomenvaylat.dll' -or
        $entryNames -contains 'Toolboxes\VaylaWFSDownloader.pyt' -or
        $entryNames -contains 'OpenSuomenvaylatToolButton.cs') {
        throw 'Package validation failed: runtime files must be under Install\.'
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
