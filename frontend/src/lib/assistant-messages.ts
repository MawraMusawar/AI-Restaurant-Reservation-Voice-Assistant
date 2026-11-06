/**
 * `assistant-error` server messages from the voice agent (backend `app/voice/bot.py`):
 * the assistant could not answer (or speak) a turn, but the call continues.
 */

export type AssistantError = {
  type: "assistant-error";
  code: "rate_limited" | "llm_error" | "tts_quota" | "tts_error";
};

export function isAssistantError(data: unknown): data is AssistantError {
  return (
    typeof data === "object" && data !== null && (data as { type?: unknown }).type === "assistant-error"
  );
}

export function describeAssistantError(error: AssistantError): string {
  switch (error.code) {
    case "rate_limited":
      return "The assistant reached the Groq free-tier rate limit and couldn't answer. Wait a few seconds, then say that again.";
    case "tts_quota":
      return "The ElevenLabs free voice credits are used up, so the assistant can't speak. They reset monthly.";
    case "tts_error":
      return "The assistant's voice failed for that reply. Please say that again.";
    default:
      return "The assistant couldn't answer just now. Please say that again.";
  }
}

/** `call-ending`: the assistant is hanging up (after its goodbye has played). */
export type CallEnding = { type: "call-ending"; reason: "assistant_ended" | "caller_silent" };

export function isCallEnding(data: unknown): data is CallEnding {
  return (
    typeof data === "object" && data !== null && (data as { type?: unknown }).type === "call-ending"
  );
}

export function describeCallEnding(ending: CallEnding): string {
  return ending.reason === "caller_silent"
    ? "The call ended because we didn't hear anything for a while."
    : "The assistant ended the call. Thanks for calling!";
}

/** What the assistant is doing while a tool runs, by tool name. */
const TOOL_ACTIVITY: Record<string, string> = {
  get_restaurant_info: "Looking up restaurant details…",
  check_availability: "Checking availability…",
  get_available_tables: "Checking tables…",
  create_reservation: "Preparing the booking…",
  get_reservation: "Finding the booking…",
  cancel_reservation: "Preparing the cancellation…",
};

export function describeToolActivity(functionName: string | undefined): string {
  return (functionName && TOOL_ACTIVITY[functionName]) || "Working on it…";
}
