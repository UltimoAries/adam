[CmdletBinding(PositionalBinding = $false)]
param(
    [Alias('p')][string]$Port,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$IdfArgs
)
$ErrorActionPreference = 'Stop'
# Avoid sharing a compiler-cache lock with a build under another Windows account.
if (-not $env:CCACHE_DIR) { $env:CCACHE_DIR = Join-Path $PSScriptRoot '..\artifacts\ccache146' }
# Separate configuration and build outputs protect the original speaker target.
$boardArgs = @('-B', 'build146', '-D', 'SDKCONFIG=sdkconfig.146', '-D', 'SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.146.defaults') + $IdfArgs
& (Join-Path $PSScriptRoot 'idf.ps1') -Port $Port -IdfArgs $boardArgs
