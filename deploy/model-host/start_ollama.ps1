param([string]$InstallRoot='C:\ADAM_VOICE')
$ErrorActionPreference='Stop'
$modelRoot=[IO.Path]::GetFullPath($InstallRoot)
$modelConfig=Get-Content -LiteralPath (Join-Path $modelRoot 'config\ollama.json') -Raw | ConvertFrom-Json
$modelRuntime=Join-Path $modelRoot ('bin\ollama\'+$modelConfig.version+'\ollama.exe')
if (-not (Test-Path -LiteralPath $modelRuntime -PathType Leaf)) { throw 'Configured Ollama executable is missing.' }
$env:OLLAMA_HOST=$modelConfig.listen
$env:OLLAMA_MODELS=Join-Path $modelRoot 'models'
$env:OLLAMA_NO_CLOUD='1'
$env:OLLAMA_CONTEXT_LENGTH='4096'
$env:OLLAMA_NUM_PARALLEL='1'
$env:OLLAMA_MAX_LOADED_MODELS='1'
$env:OLLAMA_KEEP_ALIVE='-1'
$env:OLLAMA_FLASH_ATTENTION='1'
# These changes affect this task and its child process only, not the Windows account.
$env:USERPROFILE=Join-Path $modelRoot 'data\ollama-home'
$env:LOCALAPPDATA=Join-Path $modelRoot 'data\ollama-home\AppData\Local'
$env:TEMP=Join-Path $modelRoot 'data\tmp'
$env:TMP=$env:TEMP
foreach ($modelDirectory in @($env:USERPROFILE,$env:LOCALAPPDATA,$env:TEMP)) {
    New-Item -ItemType Directory -Path $modelDirectory -Force | Out-Null
}
$modelLog=Join-Path $modelRoot 'logs\ollama.log'
if ((Test-Path -LiteralPath $modelLog) -and (Get-Item -LiteralPath $modelLog).Length -gt 10MB) {
    $modelPreviousLog=$modelLog+'.1'
    if (Test-Path -LiteralPath $modelPreviousLog) { Remove-Item -LiteralPath $modelPreviousLog }
    Move-Item -LiteralPath $modelLog -Destination $modelPreviousLog
}
Set-Location -LiteralPath $modelRoot
$modelWarmScript=Join-Path $modelRoot 'config\warm_ollama.ps1'
if (Test-Path -LiteralPath $modelWarmScript) {
    Start-Job -FilePath $modelWarmScript -ArgumentList $modelRoot | Out-Null
}
# Native stderr contains informational server logging; keep it from becoming a
# terminating NativeCommandError in Windows PowerShell 5.1.
$ErrorActionPreference='Continue'
& $modelRuntime serve *>> $modelLog
exit $LASTEXITCODE
