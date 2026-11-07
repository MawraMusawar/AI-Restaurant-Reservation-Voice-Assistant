/** Display helpers. Reservation dates and times arrive in the restaurant's local time. */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "19:30" -> "7:30 PM" */
export function formatTime(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  const suffix = h >= 12 ? "PM" : "AM";
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${suffix}`;
}

/** "2026-11-08" + "Sunday" -> "Sun 8 Nov" (no time zone shifting: it's a local date). */
export function formatDay(isoDate: string, weekday?: string): string {
  const [, month, day] = isoDate.split("-").map(Number);
  const label = `${day} ${MONTHS[month - 1]}`;
  return weekday ? `${weekday.slice(0, 3)} ${label}` : label;
}

/** An instant (ISO with offset) shown in the viewer's locale, e.g. "8 Nov, 19:04". */
export function formatInstant(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "-";
  const s = Math.round(seconds);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

export function formatPercent(rate: number | null | undefined): string {
  return rate == null ? "-" : `${Math.round(rate * 100)}%`;
}

const END_REASONS: Record<string, string> = {
  caller_hung_up: "Caller hung up",
  assistant_ended: "Assistant ended",
  caller_silent: "No answer (silence)",
  connect_timeout: "Connection timed out",
  error: "Error",
  server_shutdown: "Server stopped",
};

export function formatEndReason(reason: string | null | undefined): string {
  return reason ? (END_REASONS[reason] ?? reason) : "In progress";
}

/** Today in the restaurant's time zone is supplied by the API; this is the browser's. */
export function isoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function addDays(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  return isoDate(new Date(y, m - 1, d + days));
}
