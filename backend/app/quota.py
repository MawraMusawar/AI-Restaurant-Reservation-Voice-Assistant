"""Check the free-tier quotas before a demo.

    uv run python -m app.quota

- ElevenLabs: characters (credits) used this month and when they reset.
- Groq: requests left today and tokens left this minute, per configured model.
  Costs one tiny request per model (a few tokens).
- Deepgram: remaining account balance, if the key may read it.

Keys are read from backend/.env and never printed.
"""

import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from loguru import logger

from app.config import Settings, get_settings

TIMEOUT = httpx.Timeout(15.0)
# Below these, a demo may run out mid-call (one demo call ≈ 300-400 credits, ~10 Groq requests).
ELEVENLABS_MIN_CREDITS = 800
GROQ_MIN_REQUESTS = 50


@dataclass
class QuotaLine:
    service: str
    ok: bool
    detail: str


async def elevenlabs(client: httpx.AsyncClient, settings: Settings) -> list[QuotaLine]:
    if settings.elevenlabs_api_key is None:
        return [QuotaLine("ElevenLabs", False, "ELEVENLABS_API_KEY is not set")]
    response = await client.get(
        "https://api.elevenlabs.io/v1/user/subscription",
        headers={"xi-api-key": settings.elevenlabs_api_key.get_secret_value()},
    )
    if response.status_code != 200:
        return [
            QuotaLine(
                "ElevenLabs",
                False,
                f"HTTP {response.status_code} (the key may lack the 'User: read' permission)",
            )
        ]
    data = response.json()
    used, limit = int(data["character_count"]), int(data["character_limit"])
    left = limit - used
    reset = datetime.fromtimestamp(int(data["next_character_count_reset_unix"]), UTC)
    detail = (
        f"{left:,} of {limit:,} credits left (~{left // 350} demo calls), "
        f"resets {reset:%Y-%m-%d}, plan {data.get('tier', '?')}"
    )
    return [QuotaLine("ElevenLabs", left >= ELEVENLABS_MIN_CREDITS, detail)]


async def groq(client: httpx.AsyncClient, settings: Settings) -> list[QuotaLine]:
    if settings.groq_api_key is None:
        return [QuotaLine("Groq", False, "GROQ_API_KEY is not set")]
    fallbacks = [m.strip() for m in settings.groq_fallback_models.split(",") if m.strip()]
    lines = []
    for model in [settings.groq_model, *fallbacks]:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Say OK."}],
                "max_completion_tokens": 8,
            },
        )
        h = response.headers
        if response.status_code not in (200, 429) or "x-ratelimit-limit-requests" not in h:
            lines.append(QuotaLine(f"Groq {model}", False, f"HTTP {response.status_code}"))
            continue
        requests_left = int(h["x-ratelimit-remaining-requests"])
        detail = (
            f"{requests_left:,} of {int(h['x-ratelimit-limit-requests']):,} requests left today, "
            f"{int(h['x-ratelimit-remaining-tokens']):,} of "
            f"{int(h['x-ratelimit-limit-tokens']):,} tokens left this minute"
        )
        if response.status_code == 429:
            detail += f" - RATE LIMITED, retry after {h.get('retry-after', '?')} s"
        ok = response.status_code == 200 and requests_left >= GROQ_MIN_REQUESTS
        lines.append(QuotaLine(f"Groq {model}", ok, detail))
    return lines


async def deepgram(client: httpx.AsyncClient, settings: Settings) -> list[QuotaLine]:
    if settings.deepgram_api_key is None:
        return [QuotaLine("Deepgram", False, "DEEPGRAM_API_KEY is not set")]
    headers = {"Authorization": f"Token {settings.deepgram_api_key.get_secret_value()}"}
    projects = await client.get("https://api.deepgram.com/v1/projects", headers=headers)
    if projects.status_code != 200:
        return [QuotaLine("Deepgram", False, f"HTTP {projects.status_code} reading projects")]
    lines = []
    for project in projects.json().get("projects", []):
        balances = await client.get(
            f"https://api.deepgram.com/v1/projects/{project['project_id']}/balances",
            headers=headers,
        )
        if balances.status_code != 200:
            # The key works, but may not read billing. Not a blocker for the demo.
            lines.append(
                QuotaLine(
                    "Deepgram",
                    True,
                    f"key valid; balance not readable with this key (HTTP {balances.status_code}). "
                    "Check https://console.deepgram.com",
                )
            )
            continue
        for balance in balances.json().get("balances", []):
            amount = float(balance["amount"])
            lines.append(
                QuotaLine(
                    "Deepgram",
                    amount > 1,
                    f"${amount:,.2f} {balance.get('units', 'usd')} free credit left "
                    f"(project {project.get('name', '?')})",
                )
            )
    return lines or [QuotaLine("Deepgram", False, "no projects visible to this key")]


async def main() -> int:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        results = await asyncio.gather(
            elevenlabs(client, settings),
            groq(client, settings),
            deepgram(client, settings),
            return_exceptions=True,
        )
    lines: list[QuotaLine] = []
    for name, result in zip(("ElevenLabs", "Groq", "Deepgram"), results, strict=True):
        if isinstance(result, BaseException):
            lines.append(QuotaLine(name, False, f"check failed: {type(result).__name__}"))
        else:
            lines.extend(result)
    for line in lines:
        log = logger.info if line.ok else logger.warning
        log("{} {}: {}", "OK  " if line.ok else "LOW ", line.service, line.detail)
    return 0 if all(line.ok for line in lines) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
