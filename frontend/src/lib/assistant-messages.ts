/**
 * Server messages the voice agent sends over RTVI (see backend `app/voice/bot.py`).
 *
 * - `assistant-message`: one complete assistant reply (text until Milestone 4 speaks it).
 * - `assistant-error`: the assistant could not answer a turn; the call continues.
 */

export type AssistantMessage = { type: "assistant-message"; text: string };
export type AssistantError = { type: "assistant-error"; code: "rate_limited" | "llm_error" };

export function isAssistantMessage(data: unknown): data is AssistantMessage {
  return (
    typeof data === "object" &&
    data !== null &&
    (data as { type?: unknown }).type === "assistant-message" &&
    typeof (data as { text?: unknown }).text === "string"
  );
}

export function isAssistantError(data: unknown): data is AssistantError {
  return (
    typeof data === "object" && data !== null && (data as { type?: unknown }).type === "assistant-error"
  );
}

export function describeAssistantError(error: AssistantError): string {
  if (error.code === "rate_limited") {
    return "The assistant reached the Groq free-tier rate limit and couldn't answer. Wait a few seconds, then say that again.";
  }
  return "The assistant couldn't answer just now. Please say that again.";
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
