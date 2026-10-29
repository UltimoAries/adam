$ErrorActionPreference = 'Stop'
$cueWorkspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cueDirectory = Join-Path $cueWorkspace 'artifacts\recordings'
New-Item -ItemType Directory -Force -Path $cueDirectory | Out-Null
Add-Type -AssemblyName System.Speech
$cueSynth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $cueFormat = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
    $cueSynth.SetOutputToWaveFile((Join-Path $cueDirectory 'listen-cue-source.wav'),$cueFormat)
    $cueSynth.SpeakSsml('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US"><voice name="Microsoft David Desktop"><prosody rate="-28%" pitch="-15%">Huh?</prosody></voice></speak>')
} finally { $cueSynth.Dispose() }
& (Join-Path $cueWorkspace 'headend\.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'process_listen_cue.py')
if ($LASTEXITCODE -ne 0) { throw 'Cue processing failed' }
