param([Parameter(ValueFromRemainingArguments = $true)][string[]]$IdfArgs)
$ErrorActionPreference = 'Stop'
$env:IDF_TOOLS_PATH = 'C:\Espressif'
. C:\Espressif\esp-idf\export.ps1
Push-Location (Join-Path $PSScriptRoot '..\firmware')
try {
    idf.py @IdfArgs
    if ($LASTEXITCODE -ne 0) { throw "idf.py failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }
