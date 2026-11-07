"use client";

import { type FormEvent, useState } from "react";

import { Button, Card, ErrorBanner, Field, TextInput } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { addDays, formatTime, isoDate } from "@/lib/format";
import type { BookingProblem, Reservation } from "@/lib/types";

type Form = { customer_name: string; phone: string; date: string; time: string; guests: number; special_requests: string };

/**
 * Staff booking. The backend applies the same rules and double-booking guards as the
 * voice assistant; if the slot is full it returns nearby free times to pick from.
 */
export function NewReservationForm({ onCreated }: { onCreated: (r: Reservation) => void }) {
  const [form, setForm] = useState<Form>({
    customer_name: "",
    phone: "",
    date: addDays(isoDate(new Date()), 1),
    time: "19:00",
    guests: 2,
    special_requests: "",
  });
  const [problem, setProblem] = useState<BookingProblem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    setError(null);
    try {
      const created = await api<Reservation>("/api/reservations", {
        method: "POST",
        json: { ...form, phone: form.phone || null, special_requests: form.special_requests || null },
      });
      onCreated(created);
    } catch (err) {
      if (err instanceof ApiError && err.detail && typeof err.detail === "object" && "code" in err.detail) {
        setProblem(err.detail as BookingProblem);
      } else {
        setError(err instanceof ApiError ? err.message : "Could not create the reservation.");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="New reservation">
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Customer name">
          <TextInput required maxLength={80} value={form.customer_name} onChange={(e) => set("customer_name", e.target.value)} />
        </Field>
        <Field label="Phone" hint="Optional unless the restaurant requires it">
          <TextInput type="tel" value={form.phone} onChange={(e) => set("phone", e.target.value)} placeholder="+92 300 1234567" />
        </Field>
        <Field label="Guests">
          <TextInput type="number" min={1} max={30} required value={form.guests} onChange={(e) => set("guests", Number(e.target.value))} />
        </Field>
        <Field label="Date">
          <TextInput type="date" required value={form.date} onChange={(e) => set("date", e.target.value)} />
        </Field>
        <Field label="Time">
          <TextInput type="time" step={900} required value={form.time} onChange={(e) => set("time", e.target.value)} />
        </Field>
        <Field label="Special requests">
          <TextInput maxLength={300} value={form.special_requests} onChange={(e) => set("special_requests", e.target.value)} placeholder="e.g. window seat" />
        </Field>

        <div className="flex flex-col gap-3 sm:col-span-2 lg:col-span-3">
          {problem && (
            <ErrorBanner>
              {problem.detail}
              {problem.alternatives.length > 0 && (
                <span className="mt-2 flex flex-wrap items-center gap-2">
                  Free nearby:
                  {problem.alternatives.map((a) => (
                    <button
                      key={`${a.date}-${a.time}`}
                      type="button"
                      onClick={() => { set("date", a.date); set("time", a.time); setProblem(null); }}
                      className="rounded-full bg-white px-2.5 py-0.5 text-xs font-medium text-red-700 ring-1 ring-red-200 hover:bg-red-100 dark:bg-zinc-900 dark:text-red-300 dark:ring-red-900"
                    >
                      {formatTime(a.time)}
                    </button>
                  ))}
                </span>
              )}
            </ErrorBanner>
          )}
          {error && <ErrorBanner>{error}</ErrorBanner>}
          <div>
            <Button type="submit" disabled={busy}>{busy ? "Booking…" : "Book table"}</Button>
          </div>
        </div>
      </form>
    </Card>
  );
}
