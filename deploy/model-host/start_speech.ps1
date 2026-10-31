param([string]$InstallRoot='C:\ADAM_VOICE')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$adamRoot=[IO.Path]::GetFullPath($InstallRoot)
foreach ($adamDir in @('logs','models\speech','data\speech','config\docker-public')) {
    New-Item -ItemType Directory -Path (Join-Path $adamRoot $adamDir) -Force | Out-Null
}
$adamSpeech=Get-Content -LiteralPath (Join-Path $adamRoot 'config\speech.json') -Raw | ConvertFrom-Json
$adamSpeechUri=[Uri]$adamSpeech.url
$env:ADAM_VOICE_ROOT=$adamRoot.Replace('\','/').TrimEnd('/')
$env:SPEACHES_BIND=$adamSpeechUri.Host+':'+$adamSpeechUri.Port
$adamDocker='C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$adamEndpoint='npipe:////./pipe/dockerDesktopLinuxEngine'
$adamDockerConfig=Join-Path $adamRoot 'config\docker-public'
$adamReady=$false
# Docker Desktop starts with its Windows user. This task also runs at logon;
# CPU recognition on the head-end remains available while the engine is unavailable.
for ($adamAttempt=0;$adamAttempt -lt 60;$adamAttempt++) {
    $ErrorActionPreference='Continue'
    & $adamDocker --config $adamDockerConfig --host $adamEndpoint version --format '{{.Server.Version}}' 2>$null | Out-Null
    $adamReady=$LASTEXITCODE -eq 0
    $ErrorActionPreference='Stop'
    if ($adamReady) { break }
    Start-Sleep -Seconds 5
}
if (-not $adamReady) { exit 0 }
$ErrorActionPreference='Continue'
& $adamDocker --config $adamDockerConfig --host $adamEndpoint compose -p adam-speech -f (Join-Path $adamRoot 'config\speaches.compose.yaml') up -d 2>&1 | Out-File (Join-Path $adamRoot 'logs\speech-startup.log') -Encoding utf8
$adamCode=$LASTEXITCODE
$ErrorActionPreference='Stop'
if ($adamCode -ne 0) { throw 'Speech container startup failed. See speech-startup.log.' }
$adamKey=(Get-Content -LiteralPath (Join-Path $adamRoot 'config\speaches.env') | Where-Object { $_.StartsWith('API_KEY=') }).Substring(8).Trim()
for ($adamAttempt=0;$adamAttempt -lt 60;$adamAttempt++) {
    try { Invoke-RestMethod -Uri ($adamSpeech.url+'/health') -Headers @{Authorization='Bearer '+$adamKey} -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Seconds 2 }
}
$adamTimer=[Diagnostics.Stopwatch]::StartNew()
try {
    Invoke-RestMethod -Method Post -Uri ($adamSpeech.url+'/api/ps/'+$adamSpeech.model) -Headers @{Authorization='Bearer '+$adamKey} -TimeoutSec 180 | Out-Null
    @{ready=$true;model=$adamSpeech.model;seconds=$adamTimer.Elapsed.TotalSeconds;checked_at_utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content (Join-Path $adamRoot 'logs\speech-warmup.json') -Encoding UTF8
} catch {
    @{ready=$false;model=$adamSpeech.model;checked_at_utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content (Join-Path $adamRoot 'logs\speech-warmup.json') -Encoding UTF8
    throw 'Speech model warm-up failed. The head-end can use local recognition.'
}
