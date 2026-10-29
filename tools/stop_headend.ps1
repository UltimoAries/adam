$ErrorActionPreference = 'Stop'
$voiceWorkspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$voicePidPath = Join-Path $voiceWorkspace 'headend\data\server.pid'
if (-not (Test-Path -LiteralPath $voicePidPath)) { Write-Output 'No saved head-end process.'; exit }
$voiceProcessId = [int](Get-Content -LiteralPath $voicePidPath)
$voiceProcess = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $voiceProcessId)
if ($null -eq $voiceProcess) { Write-Output 'The saved head-end process has already stopped.'; exit }
if ($voiceProcess.CommandLine -notmatch '(?i)-m\s+headend\.server\b' -or $voiceProcess.CommandLine -notlike ('*' + $voiceWorkspace + '*')) {
    throw 'The saved process no longer matches this workspace head-end. Refusing to stop it.'
}
$voiceStopResult = & taskkill.exe /PID $voiceProcessId /T /F 2>&1
if ($LASTEXITCODE -ne 0) { throw ('Could not stop the verified head-end process tree: ' + ($voiceStopResult -join ' ')) }
Write-Output 'ADAM (Automation Display and Mapping) stopped.'
