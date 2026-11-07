# Builds the caller audio for the voice scenarios with the Windows speech engine
# (System.Speech, no downloads or keys). Output: e2e/.audio/*.wav, 16 kHz mono.
# Chrome plays the file as the microphone (--use-file-for-fake-audio-capture) and
# loops it, so each file starts with silence (connect + greeting) and leaves room
# for the assistant to answer between lines.
#
#   powershell -ExecutionPolicy Bypass -File e2e\make_audio.ps1

$ErrorActionPreference = "Stop"
$out = Join-Path $PSScriptRoot ".audio"
New-Item -ItemType Directory -Force $out | Out-Null
Add-Type -AssemblyName System.Speech
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)

function Say([string]$text) {
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $synth.SelectVoice("Microsoft Zira Desktop")
    $stream = New-Object System.IO.MemoryStream
    $synth.SetOutputToAudioStream($stream, $format)
    $synth.Speak($text)
    $synth.Dispose()
    return ,$stream.ToArray()
}

function Silence([double]$seconds) { return ,(New-Object byte[] ([int](16000 * $seconds) * 2)) }

function Write-Wav([string]$name, [object[]]$parts) {
    $pcm = New-Object System.Collections.Generic.List[byte]
    foreach ($p in $parts) { $pcm.AddRange([byte[]]$p) }
    $path = Join-Path $out $name
    $w = New-Object System.IO.BinaryWriter([System.IO.File]::Create($path))
    $data = $pcm.ToArray()
    $w.Write([Text.Encoding]::ASCII.GetBytes("RIFF")); $w.Write([int](36 + $data.Length))
    $w.Write([Text.Encoding]::ASCII.GetBytes("WAVEfmt ")); $w.Write([int]16); $w.Write([int16]1); $w.Write([int16]1)
    $w.Write([int]16000); $w.Write([int]32000); $w.Write([int16]2); $w.Write([int16]16)
    $w.Write([Text.Encoding]::ASCII.GetBytes("data")); $w.Write([int]$data.Length); $w.Write($data)
    $w.Close()
    Write-Output ("{0}  {1:N1} s" -f $name, ($data.Length / 32000))
}

# A full booking: request, name, confirmation, goodbye. The gaps leave room for the
# availability check, the read-back and the confirmation with the reference.
Write-Wav "booking_call.wav" @(
    (Silence 12),
    (Say "Hi, I'd like to book a table for two people tomorrow at seven PM."), (Silence 16),
    (Say "The name is Sara Ahmed."), (Silence 18),
    (Say "Yes, that's correct."), (Silence 22),
    (Say "That's all, thank you. Goodbye!"), (Silence 40)
)
