/** Mirrors `GET /api/voice/status` (backend/app/voice/router.py: VoiceStatusOut). */
export type VoiceStatus = {
  ready: boolean;
  missing_credentials: string[];
  active_sessions: number;
  max_sessions: number;
  detail: string | null;
  /** False: calls work, but the assistant can't check availability or book (no database). */
  bookings_online: boolean;
};

/** Ask the backend whether a call can start right now (credentials configured, a line free). */
export async function fetchVoiceStatus(signal?: AbortSignal): Promise<VoiceStatus> {
  const res = await fetch("/api/voice/status", { cache: "no-store", signal });
  if (!res.ok) throw new Error(`Voice status check failed (HTTP ${res.status})`);
  return (await res.json()) as VoiceStatus;
}

/** Friendly text for an RTVI `error` message sent by the server pipeline. */
export function describeServerError(message: string | undefined): string {
  const text = message ?? "";
  if (/deepgram|stt|transcri/i.test(text)) {
    if (/40[13]|reject|invalid|unauthori[sz]ed|credit|quota|payment/i.test(text)) {
      return "Speech recognition was rejected by Deepgram. Check the API key and remaining free credit. The call has ended.";
    }
    return "Speech recognition is having trouble connecting to Deepgram. Retrying…";
  }
  return "The assistant ran into a technical problem.";
}
