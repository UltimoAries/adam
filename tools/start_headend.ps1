param([switch]$OpenBrowser,[switch]$Lan)
$ErrorActionPreference = 'Stop'
$voiceWorkspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$voicePython = Join-Path $voiceWorkspace 'headend\.venv\Scripts\python.exe'
$voiceData = Join-Path $voiceWorkspace 'headend\data'
$voiceUrl = 'http://127.0.0.1:8765'
if (-not (Test-Path -LiteralPath $voicePython)) { throw 'Create headend/.venv and install headend/requirements-lock.txt first.' }
New-Item -ItemType Directory -Force -Path $voiceData | Out-Null
$voiceReady = $false
try {
    $voiceHealth = Invoke-RestMethod -Uri ($voiceUrl + '/api/health') -TimeoutSec 2
    $voiceReady = $voiceHealth.status -eq 'ok' -and $voiceHealth.model -eq 'base.en'
} catch {}
if (-not $voiceReady) {
    $voiceArguments = @('-m','headend.server')
    if ($Lan) { $voiceArguments += '--lan' }
    $voiceProcess = Start-Process -FilePath $voicePython -ArgumentList $voiceArguments -WorkingDirectory $voiceWorkspace -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $voiceData 'server.stdout.log') -RedirectStandardError (Join-Path $voiceData 'server.stderr.log')
    Set-Content -LiteralPath (Join-Path $voiceData 'server.pid') -Value $voiceProcess.Id
    for ($voiceAttempt = 0; $voiceAttempt -lt 90; $voiceAttempt++) {
        Start-Sleep -Milliseconds 400
        try {
            $voiceHealth = Invoke-RestMethod -Uri ($voiceUrl + '/api/health') -TimeoutSec 1
            if ($voiceHealth.status -eq 'ok' -and $voiceHealth.model -eq 'base.en') { $voiceReady = $true; break }
        } catch {}
        if ($voiceProcess.HasExited) { break }
    }
}
if (-not $voiceReady) { throw ('Head-end startup failed. Read ' + (Join-Path $voiceData 'server.stderr.log')) }
if ($voiceHealth.version -ne '0.6' -or ($Lan -and $voiceHealth.network -ne 'lan')) { throw 'Stop the older/different-mode server with tools/stop_headend.ps1, then start again.' }
Write-Output ('ADAM (Automation Display and Mapping): ' + $voiceUrl)
if ($OpenBrowser) { Start-Process $voiceUrl }
