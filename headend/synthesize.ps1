$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$voiceRequest = [Console]::In.ReadToEnd() | ConvertFrom-Json
Add-Type -AssemblyName System.Speech
$voiceSynth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $voiceFormat = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
    $voiceSynth.SetOutputToWaveFile([string]$voiceRequest.path, $voiceFormat)
    $voiceSynth.Speak([string]$voiceRequest.text)
} finally { $voiceSynth.Dispose() }
