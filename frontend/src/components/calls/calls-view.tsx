"use client";

import { useState } from "react";

import { Badge, Card, Empty, ErrorBanner, Loading, PageHeader, ReservationStatusBadge } from "@/components/ui";
import { formatDay, formatDuration, formatEndReason, formatInstant, formatTime } from "@/lib/format";
import type { CallDetail, CallSummary } from "@/lib/types";
import { useApi } from "@/lib/use-api";

function outcome(c: CallSummary) {
  if (c.live) return <Badge tone="info">Live</Badge>;
  if (c.booking_reference) return <Badge tone="good">Booked {c.booking_reference}</Badge>;
  if (c.status === "error") return <Badge tone="critical">Error</Badge>;
  return <Badge tone="neutral">No booking</Badge>;
}

export function CallsView() {
  const { data, error, loading } = useApi<CallSummary[]>("/api/calls", 10_000);
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Call logs" description="Every voice call: what was said, how it ended, and the booking it made." />
      {error && <ErrorBanner>{error.message}</ErrorBanner>}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <Card title="Calls">
          {loading && !data ? (
            <Loading />
          ) : !data || data.length === 0 ? (
            <Empty>No calls yet.</Empty>
          ) : (
            <ul className="-mx-5 -my-5 divide-y divide-zinc-100 dark:divide-zinc-800" aria-label="Calls">
              {data.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => setSelected(c.id)}
                    aria-pressed={selected === c.id}
                    className={`flex w-full items-start justify-between gap-3 px-5 py-3 text-left text-sm transition ${
                      selected === c.id ? "bg-emerald-50 dark:bg-emerald-950/40" : "hover:bg-zinc-50 dark:hover:bg-zinc-800/50"
                    }`}
                  >
                    <span className="min-w-0">
                      <span className="block truncate font-medium">{c.first_request ?? "(no speech)"}</span>
                      <span className="block text-xs text-zinc-500 dark:text-zinc-400">
                        {formatInstant(c.started_at)} · {formatDuration(c.duration_seconds)} · {c.message_count} messages · {formatEndReason(c.end_reason)}
                      </span>
                    </span>
                    {outcome(c)}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        {selected ? <CallPanel id={selected} /> : (
          <Card title="Conversation"><Empty>Select a call to see its transcript.</Empty></Card>
        )}
      </div>
    </div>
  );
}

function metric(value: unknown): string {
  return Array.isArray(value) && value.length
    ? value.map((v) => `${Math.round(Number(v))} ms`).join(", ")
    : "-";
}

function CallPanel({ id }: { id: string }) {
  const { data, error, loading } = useApi<CallDetail>(`/api/calls/${encodeURIComponent(id)}`);
  if (loading && !data) return <Card title="Conversation"><Loading /></Card>;
  if (!data) return <ErrorBanner>{error?.message ?? "Could not load the call."}</ErrorBanner>;
  const m = data.metrics;

  return (
    <Card title="Conversation" actions={<span className="font-mono text-[11px] text-zinc-400">{data.id.slice(-12)}</span>}>
      <dl className="mb-5 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
        <Info label="Started" value={formatInstant(data.started_at)} />
        <Info label="Ended" value={data.ended_at ? formatInstant(data.ended_at) : "In progress"} />
        <Info label="Outcome" value={formatEndReason(data.end_reason)} />
        <Info label="Reply time (heard)" value={metric(m.voice_latencies_ms)} />
        <Info label="Transcription" value={metric(m.stt_latencies_ms)} />
        <Info label="Startup" value={m.startup_ms ? `${Math.round(Number(m.startup_ms))} ms` : "-"} />
      </dl>

      {data.reservations.map((r) => (
        <div key={r.id} className="mb-4 flex items-center justify-between gap-3 rounded-xl bg-emerald-50 px-4 py-3 text-sm dark:bg-emerald-950/40">
          <span>
            Booking <span className="font-mono">{r.booking_reference}</span>: {r.customer_name}, {r.guests} guests,{" "}
            {formatDay(r.date, r.weekday)} {formatTime(r.time)}
          </span>
          <ReservationStatusBadge status={r.status} />
        </div>
      ))}
      {data.error && <div className="mb-4"><ErrorBanner>{data.error}</ErrorBanner></div>}
      {data.errors.length > 0 && !data.error && (
        <details className="mb-4 text-xs text-zinc-500 dark:text-zinc-400">
          <summary>{data.errors.length} recovered issue(s)</summary>
          <ul className="mt-2 list-disc pl-5">{data.errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
        </details>
      )}

      {data.transcript.length === 0 ? (
        <Empty>Nothing was said.</Empty>
      ) : (
        <ol className="flex max-h-[28rem] flex-col gap-2 overflow-y-auto pr-1" aria-label="Transcript">
          {data.transcript.map((t, i) => (
            <li key={i} className={`flex ${t.role === "user" ? "justify-end" : "justify-start"}`}>
              <span
                className={`max-w-[85%] rounded-2xl px-3.5 py-2 text-sm ${
                  t.role === "user"
                    ? "bg-sky-50 text-sky-950 dark:bg-sky-950/60 dark:text-sky-100"
                    : "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
                }`}
              >
                <span className="mb-0.5 block text-[10px] font-semibold uppercase tracking-wide opacity-60">
                  {t.role === "user" ? "Caller" : "Assistant"}
                  {t.interrupted && " · interrupted"}
                </span>
                {t.content}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-zinc-500 dark:text-zinc-400">{label}</dt>
      <dd className="mt-0.5 font-medium">{value}</dd>
    </div>
  );
}
