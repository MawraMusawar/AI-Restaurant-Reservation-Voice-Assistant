"use client";

import { useMemo, useState } from "react";

import { NewReservationForm } from "@/components/reservations/new-reservation-form";
import { Button, Card, Empty, ErrorBanner, Field, Loading, PageHeader, ReservationStatusBadge, Select, SuccessBanner, TextInput } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { addDays, formatDay, formatTime, isoDate } from "@/lib/format";
import type { Reservation } from "@/lib/types";
import { useApi } from "@/lib/use-api";

type Range = "today" | "week" | "upcoming" | "past" | "all";

const RANGES: { value: Range; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "week", label: "Next 7 days" },
  { value: "upcoming", label: "All upcoming" },
  { value: "past", label: "Past 30 days" },
  { value: "all", label: "Everything" },
];

function rangeParams(range: Range): Record<string, string> {
  const today = isoDate(new Date());
  switch (range) {
    case "today":
      return { from: today, to: today };
    case "week":
      return { from: today, to: addDays(today, 6) };
    case "upcoming":
      return { from: today };
    case "past":
      return { from: addDays(today, -30), to: addDays(today, -1) };
    default:
      return {};
  }
}

export function ReservationsView() {
  const [range, setRange] = useState<Range>("week");
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const path = useMemo(() => {
    const params = new URLSearchParams(rangeParams(range));
    if (status) params.set("status", status);
    if (query.trim()) params.set("q", query.trim());
    return `/api/reservations?${params}`;
  }, [range, status, query]);
  const { data, error, loading, reload } = useApi<Reservation[]>(path, 20_000);

  const cancel = async (r: Reservation) => {
    if (!window.confirm(`Cancel ${r.booking_reference} for ${r.customer_name} (${r.guests}) on ${formatDay(r.date, r.weekday)} at ${formatTime(r.time)}?`)) return;
    setActionError(null);
    try {
      await api(`/api/reservations/${r.booking_reference}/cancel`, { method: "POST" });
      setMessage(`Cancelled ${r.booking_reference}. The table is free again.`);
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Could not cancel.");
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Reservations"
        description="Bookings from voice calls and staff. Times are the restaurant's local time."
        actions={
          <Button onClick={() => { setShowForm((v) => !v); setMessage(null); }}>
            {showForm ? "Close form" : "New reservation"}
          </Button>
        }
      />

      {showForm && (
        <NewReservationForm
          onCreated={(r) => {
            setShowForm(false);
            setMessage(`Booked ${r.booking_reference}: ${r.customer_name}, ${r.guests} guests, ${formatDay(r.date, r.weekday)} at ${formatTime(r.time)} (table ${r.table}).`);
            reload();
          }}
        />
      )}
      {message && <SuccessBanner>{message}</SuccessBanner>}
      {actionError && <ErrorBanner>{actionError}</ErrorBanner>}

      <Card>
        <div className="mb-4 grid gap-3 sm:grid-cols-3">
          <Field label="When">
            <Select value={range} onChange={(e) => setRange(e.target.value as Range)}>
              {RANGES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </Select>
          </Field>
          <Field label="Status">
            <Select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Any</option>
              <option value="confirmed">Confirmed</option>
              <option value="cancelled">Cancelled</option>
              <option value="completed">Completed</option>
              <option value="no_show">No-show</option>
            </Select>
          </Field>
          <Field label="Search">
            <TextInput type="search" placeholder="Name, phone or reference" value={query} onChange={(e) => setQuery(e.target.value)} />
          </Field>
        </div>

        {error && <ErrorBanner>{error.message}</ErrorBanner>}
        {loading && !data ? (
          <Loading />
        ) : !data || data.length === 0 ? (
          <Empty>No reservations match.</Empty>
        ) : (
          <div className="-mx-5 overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm" aria-label="Reservations">
              <thead>
                <tr className="border-b border-zinc-200 text-left text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
                  <th className="px-5 py-2 font-medium">When</th>
                  <th className="px-3 py-2 font-medium">Customer</th>
                  <th className="px-3 py-2 text-right font-medium">Guests</th>
                  <th className="px-3 py-2 font-medium">Table</th>
                  <th className="px-3 py-2 font-medium">Reference</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-5 py-2 text-right font-medium"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {data.map((r) => (
                  <tr key={r.id} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800">
                    <td className="px-5 py-3 whitespace-nowrap">
                      <span className="font-medium">{formatTime(r.time)}</span>
                      <span className="block text-xs text-zinc-500 dark:text-zinc-400">{formatDay(r.date, r.weekday)}</span>
                    </td>
                    <td className="px-3 py-3">
                      {r.customer_name}
                      <span className="block text-xs text-zinc-500 dark:text-zinc-400">
                        {r.phone ?? "no phone"}{r.special_requests ? ` · ${r.special_requests}` : ""}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums">{r.guests}</td>
                    <td className="px-3 py-3 whitespace-nowrap">{r.table} <span className="text-xs text-zinc-500 dark:text-zinc-400">{r.area}</span></td>
                    <td className="px-3 py-3 font-mono text-xs whitespace-nowrap">
                      {r.booking_reference}
                      <span className="block font-sans text-zinc-500 dark:text-zinc-400">via {r.source}</span>
                    </td>
                    <td className="px-3 py-3"><ReservationStatusBadge status={r.status} /></td>
                    <td className="px-5 py-3 text-right">
                      {r.status === "confirmed" && (
                        <Button variant="secondary" onClick={() => cancel(r)} aria-label={`Cancel ${r.booking_reference}`}>
                          Cancel
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
