param([Parameter(ValueFromRemainingArguments = $true)][string[]]$IdfArgs)
$ErrorActionPreference = 'Stop'

if (-not (Get-Command idf.py -ErrorAction SilentlyContinue)) {
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:IDF_PATH) {
        $candidates.Add((Join-Path $env:IDF_PATH 'export.ps1'))
    }
    $candidates.Add('C:\Espressif\esp-idf\export.ps1')
    if ($env:USERPROFILE) {
        $userEsp = Join-Path $env:USERPROFILE '.espressif'
        $candidates.Add((Join-Path $userEsp 'esp-idf\export.ps1'))
        if (Test-Path -LiteralPath (Join-Path $userEsp 'frameworks')) {
            Get-ChildItem -Path (Join-Path $userEsp 'frameworks') -Filter 'export.ps1' -Recurse -Depth 3 -ErrorAction SilentlyContinue |
                ForEach-Object { $candidates.Add($_.FullName) }
        }
    }

    [string]$idfExport = $null
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            $idfExport = $candidate
            break
        }
    }
    if (-not $idfExport) {
        throw 'ESP-IDF environment not found (idf.py is not on PATH). Set $env:IDF_PATH to your ESP-IDF directory (containing export.ps1) and optionally $env:IDF_TOOLS_PATH, or run from an activated ESP-IDF shell.'
    }

    if (-not $env:IDF_TOOLS_PATH) {
        $idfParent = Split-Path (Split-Path $idfExport -Parent) -Parent
        if ($idfParent -and (Test-Path -LiteralPath (Join-Path $idfParent 'tools'))) {
            $env:IDF_TOOLS_PATH = $idfParent
        }
    }

    . $idfExport
    if (-not (Get-Command idf.py -ErrorAction SilentlyContinue)) {
        throw "ESP-IDF environment activation failed via $idfExport"
    }
}

Push-Location (Join-Path $PSScriptRoot '..\firmware')
try {
    idf.py @IdfArgs
    if ($LASTEXITCODE -ne 0) { throw "idf.py failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }
