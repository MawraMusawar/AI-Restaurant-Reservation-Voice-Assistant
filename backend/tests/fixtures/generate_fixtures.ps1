# Regenerates the speech WAV fixtures used by the Deepgram integration tests.
# Uses the Windows built-in speech engine (System.Speech), so it needs no downloads or API keys.
# Output: 16 kHz, 16-bit, mono WAV files next to this script.
#
#   powershell -ExecutionPolicy Bypass -File backend\tests\fixtures\generate_fixtures.ps1

Add-Type -AssemblyName System.Speech
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono
)

$fixtures = @(
    @{ File = "booking_normal.wav"; Voice = "Microsoft David Desktop"; Rate = 0;
       Text = "Hi, I would like to book a table for four people tomorrow at seven PM." },
    @{ File = "correction_fast.wav"; Voice = "Microsoft Zira Desktop"; Rate = 4;
       Text = "Actually, can we change that to six thirty, under the name Sarah?" },
    @{ File = "question_slow.wav"; Voice = "Microsoft Zira Desktop"; Rate = -4;
       Text = "What time do you close on Sunday?" }
)

foreach ($f in $fixtures) {
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $synth.SelectVoice($f.Voice)
    $synth.Rate = $f.Rate
    $path = Join-Path $PSScriptRoot $f.File
    $synth.SetOutputToWaveFile($path, $format)
    $synth.Speak($f.Text)
    $synth.Dispose()
    Write-Output ("{0}  ({1:N0} bytes)" -f $f.File, (Get-Item $path).Length)
}
