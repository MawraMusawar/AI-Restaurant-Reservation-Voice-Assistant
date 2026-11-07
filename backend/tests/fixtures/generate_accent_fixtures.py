"""Generate accented caller fixtures with ElevenLabs premade voices (one-off, ~120 credits).

Windows' speech engine only has US English voices, so British and Australian
callers come from ElevenLabs. Output: 16 kHz mono WAVs next to this file.

    cd backend
    uv run python tests/fixtures/generate_accent_fixtures.py
"""

import sys
import wave
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.config import get_settings  # noqa: E402

HERE = Path(__file__).parent
TEXT = "Hello, could I book a table for four people tomorrow at seven thirty, please?"
VOICES = {
    "accent_british_male.wav": "JBFqnCBsd6RMkjVDRZzb",  # George - British
    "accent_british_female.wav": "pFZP5JQG7iQjIQuC4Bku",  # Lily - British
    "accent_australian_male.wav": "IKne3meq5aSn9XLyUdCD",  # Charlie - Australian
}


def main() -> None:
    key = get_settings().elevenlabs_api_key
    assert key is not None, "ELEVENLABS_API_KEY is not set"
    for name, voice in VOICES.items():
        response = httpx.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
            params={"output_format": "pcm_16000"},
            headers={"xi-api-key": key.get_secret_value()},
            json={"text": TEXT, "model_id": "eleven_flash_v2_5"},
            timeout=60,
        )
        response.raise_for_status()
        silence = b"\x00\x00" * 16000  # 1 s of trailing silence for the VAD
        with wave.open(str(HERE / name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(response.content + silence)
        print(f"{name}: {len(response.content) / 32000:.1f} s")


if __name__ == "__main__":
    main()
