# Verify argument forwarding without ESP-IDF, serial discovery, or flash writes.
$ErrorActionPreference = 'Stop'
$previousIdf = Get-Item Function:\global:idf.py -ErrorAction SilentlyContinue
$previousCache = $env:CCACHE_DIR
$originalDirectory = (Get-Location).Path
try {
    function global:idf.py {
        $global:AdamTestIdfArgs = @($args)
        $global:LASTEXITCODE = 0
    }
    foreach ($wrapper in @('idf.ps1', 'idf146.ps1')) {
        $script = Join-Path $PSScriptRoot $wrapper
        & $script -p COM99 flash
        if ($global:AdamTestIdfArgs[0] -ne '--port' -or $global:AdamTestIdfArgs[1] -ne 'COM99' -or
            $global:AdamTestIdfArgs[-1] -ne 'flash') { throw "$wrapper lost -p or flash" }
        & $script -Port COM98 -IdfArgs @('monitor')
        if ($global:AdamTestIdfArgs[1] -ne 'COM98' -or $global:AdamTestIdfArgs[-1] -ne 'monitor') {
            throw "$wrapper lost named Port or argument array"
        }
        & $script build
        if ($global:AdamTestIdfArgs -contains '--port' -or $global:AdamTestIdfArgs[-1] -ne 'build') {
            throw "$wrapper treated build as a port"
        }
        if ($wrapper -eq 'idf146.ps1') {
            if ($global:AdamTestIdfArgs -notcontains 'build146' -or
                $global:AdamTestIdfArgs -notcontains 'SDKCONFIG=sdkconfig.146') {
                throw '1.46 wrapper lost board isolation'
            }
        }
        if ((Get-Location).Path -ne $originalDirectory) { throw "$wrapper changed the working directory" }
        Write-Output "PASS $wrapper preserves explicit port, command, and build arguments"
    }
} finally {
    if ($previousIdf) { Set-Item Function:\global:idf.py -Value $previousIdf.ScriptBlock }
    else { Remove-Item Function:\global:idf.py -ErrorAction SilentlyContinue }
    Remove-Variable AdamTestIdfArgs -Scope Global -ErrorAction SilentlyContinue
    $env:CCACHE_DIR = $previousCache
}
