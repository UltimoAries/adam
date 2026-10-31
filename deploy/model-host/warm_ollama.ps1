param([string]$InstallRoot='C:\ADAM_VOICE')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$modelConfig=Get-Content -LiteralPath (Join-Path $InstallRoot 'config\ollama.json') -Raw | ConvertFrom-Json
$modelListen=[string]$modelConfig.listen
if ($modelListen.StartsWith('0.0.0.0:')) { $modelListen='127.0.0.1:'+$modelListen.Substring(8) }
$modelAddress='http://'+$modelListen
$modelReady=$false
for ($modelAttempt=0;$modelAttempt -lt 60;$modelAttempt++) {
    try {
        $modelTags=Invoke-RestMethod -Uri ($modelAddress+'/api/tags') -TimeoutSec 2
        if ($modelConfig.preferred_model -in $modelTags.models.name) { $modelReady=$true;break }
    } catch { }
    Start-Sleep -Seconds 2
}
if (-not $modelReady) { exit 0 }  # A newly installed host may not have downloaded its model yet.
$modelName=$modelConfig.preferred_model
if ($modelConfig.model_alias) {
    Invoke-RestMethod -Method Post -Uri ($modelAddress+'/api/copy') -ContentType application/json -Body (@{source=$modelName;destination=$modelConfig.model_alias} | ConvertTo-Json) -TimeoutSec 15 | Out-Null
    $modelName=$modelConfig.model_alias
}
$modelSystem='You are ADAM, a home voice assistant. Answer in one short sentence, at most 30 words and 180 characters. Use plain speech without markup. You cannot control devices or access live weather, news or personal data. Never claim to have performed an action. Admit uncertainty.'
$modelRequest=@{
    model=$modelName;stream=$false;think=$false;keep_alive=-1
    messages=@(@{role='system';content=$modelSystem},@{role='user';content='Say ready.'})
    options=@{num_ctx=4096;num_predict=8;temperature=0.3}
} | ConvertTo-Json -Depth 5
$modelTiming=[Diagnostics.Stopwatch]::StartNew()
try {
    $modelResponse=Invoke-RestMethod -Method Post -Uri ($modelAddress+'/api/chat') -ContentType application/json -Body $modelRequest -TimeoutSec 180
    $modelTiming.Stop()
    @{model=$modelConfig.preferred_model;ready=$true;seconds=$modelTiming.Elapsed.TotalSeconds;checked_at_utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $InstallRoot 'logs\ollama-warmup.json') -Encoding UTF8
} catch {
    @{model=$modelConfig.preferred_model;ready=$false;checked_at_utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $InstallRoot 'logs\ollama-warmup.json') -Encoding UTF8
}
