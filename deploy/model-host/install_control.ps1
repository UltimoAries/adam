[CmdletBinding()]
param(
    [string]$InstallRoot='C:\ADAM_VOICE',
    [string]$PythonPath='',
    [string]$LogonUser=$env:USERNAME,
    [switch]$SkipSpeech
)
$ErrorActionPreference='Stop'
$adamIdentity=[Security.Principal.WindowsIdentity]::GetCurrent()
$adamPrincipal=[Security.Principal.WindowsPrincipal]::new($adamIdentity)
if (-not $adamPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run install_control.ps1 from an elevated (Run as administrator) PowerShell window.'
}
$adamRoot=[IO.Path]::GetFullPath($InstallRoot)
foreach ($adamFolder in @('bin','config','models','logs','data')) {
    New-Item -ItemType Directory -Path (Join-Path $adamRoot $adamFolder) -Force | Out-Null
}
$adamSourceRoot=$PSScriptRoot
if (Test-Path -LiteralPath (Join-Path $adamSourceRoot 'adam_control.pyw') -PathType Leaf) {
    Copy-Item -LiteralPath (Join-Path $adamSourceRoot 'adam_control.pyw') -Destination (Join-Path $adamRoot 'ADAM_GPU_Control.pyw') -Force
}
foreach ($adamScript in @('start_ollama.ps1','warm_ollama.ps1','start_speech.ps1','speaches.compose.yaml')) {
    $adamCandidate=Join-Path $adamSourceRoot $adamScript
    if (Test-Path -LiteralPath $adamCandidate -PathType Leaf) {
        Copy-Item -LiteralPath $adamCandidate -Destination (Join-Path $adamRoot ('config\'+$adamScript)) -Force
    }
}
$adamPython=$PythonPath
if ([string]::IsNullOrWhiteSpace($adamPython)) {
    $adamCmd=Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($adamCmd) { $adamPython=$adamCmd.Source }
}
if ([string]::IsNullOrWhiteSpace($adamPython)) {
    try {
        $adamPyExe=(& py -3 -c "import pathlib, sys; print(pathlib.Path(sys.executable).with_name('pythonw.exe'))" 2>$null | Select-Object -First 1).Trim()
        if ($adamPyExe -and (Test-Path -LiteralPath $adamPyExe -PathType Leaf)) { $adamPython=$adamPyExe }
    } catch { }
}
if ([string]::IsNullOrWhiteSpace($adamPython)) {
    $adamSearch=@(
        Get-ChildItem -Path "$env:LOCALAPPDATA\Programs\Python\Python*\pythonw.exe" -ErrorAction SilentlyContinue
        Get-ChildItem -Path 'C:\Python*\pythonw.exe' -ErrorAction SilentlyContinue
        Get-ChildItem -Path 'C:\Program Files\Python*\pythonw.exe' -ErrorAction SilentlyContinue
    ) | Sort-Object FullName -Descending | Select-Object -First 1
    if ($adamSearch) { $adamPython=$adamSearch.FullName }
}
if ([string]::IsNullOrWhiteSpace($adamPython) -or -not (Test-Path -LiteralPath $adamPython -PathType Leaf)) {
    throw 'Python with Tkinter (pythonw.exe) was not found. Pass -PythonPath "C:\Path\To\pythonw.exe".'
}
foreach ($adamFile in @('ADAM_GPU_Control.pyw','config\start_ollama.ps1','config\warm_ollama.ps1','config\ollama.json')) {
    if (-not (Test-Path -LiteralPath (Join-Path $adamRoot $adamFile) -PathType Leaf)) {
        throw ('Missing required file under '+$adamRoot+': '+$adamFile)
    }
}
$adamPowerShell=Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$adamPrincipalSpec=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$adamSettings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

$adamOllamaAction=New-ScheduledTaskAction -Execute $adamPowerShell -Argument ('-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+(Join-Path $adamRoot 'config\start_ollama.ps1')+'" -InstallRoot "'+$adamRoot+'"') -WorkingDirectory $adamRoot
Register-ScheduledTask -TaskName 'ADAM-Ollama' -Action $adamOllamaAction -Trigger (New-ScheduledTaskTrigger -AtStartup) -Principal $adamPrincipalSpec -Settings $adamSettings -Description 'ADAM GPU Ollama conversation service; local head-end fallback remains available.' -Force | Out-Null

$adamSpeechTask=$null
if (-not $SkipSpeech) {
    foreach ($adamSpeechFile in @('config\start_speech.ps1','config\speaches.compose.yaml','config\speaches.env','config\speech.json')) {
        if (-not (Test-Path -LiteralPath (Join-Path $adamRoot $adamSpeechFile) -PathType Leaf)) {
            throw ('Missing speech configuration file under '+$adamRoot+': '+$adamSpeechFile+' (or pass -SkipSpeech)')
        }
    }
    $adamTriggers=@((New-ScheduledTaskTrigger -AtStartup))
    if (-not [string]::IsNullOrWhiteSpace($LogonUser)) {
        $adamTriggers+=New-ScheduledTaskTrigger -AtLogOn -User $LogonUser
    }
    $adamSpeechAction=New-ScheduledTaskAction -Execute $adamPowerShell -Argument ('-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+(Join-Path $adamRoot 'config\start_speech.ps1')+'" -InstallRoot "'+$adamRoot+'"') -WorkingDirectory $adamRoot
    Register-ScheduledTask -TaskName 'ADAM-Speech' -Action $adamSpeechAction -Trigger $adamTriggers -Principal $adamPrincipalSpec -Settings $adamSettings -Description 'ADAM GPU speech recognition; head-end CPU fallback remains available.' -Force | Out-Null
    $adamSpeechTask='ADAM-Speech'
}

$adamShell=New-Object -ComObject WScript.Shell
$adamShortcutPath=Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'ADAM GPU Control.lnk'
$adamShortcut=$adamShell.CreateShortcut($adamShortcutPath)
$adamShortcut.TargetPath=$adamPython
$adamShortcut.Arguments='"'+(Join-Path $adamRoot 'ADAM_GPU_Control.pyw')+'" --root "'+$adamRoot+'"'
$adamShortcut.WorkingDirectory=$adamRoot
$adamShortcut.Description='Start, stop and select ADAM GPU models'
$adamShortcut.Save()
@{shortcut=$adamShortcutPath;app=(Join-Path $adamRoot 'ADAM_GPU_Control.pyw');python=$adamPython;ollama_task='ADAM-Ollama';speech_task=$adamSpeechTask} | ConvertTo-Json
