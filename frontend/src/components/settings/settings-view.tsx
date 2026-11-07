"use client";

import { type FormEvent, type ReactNode, useState } from "react";

import { BackendStatus } from "@/components/backend-status";
import { Badge, Button, Card, ErrorBanner, Field, Loading, PageHeader, Select, SuccessBanner, TextInput } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { AssistantSettings, DayHours, RestaurantSettings, SettingsBundle, TableRow } from "@/lib/types";
import { useApi } from "@/lib/use-api";

const TABS = ["Restaurant & rules", "Opening hours", "Tables", "Assistant", "System"] as const;
type Tab = (typeof TABS)[number];
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function SettingsView() {
  const { data, error, loading, reload } = useApi<SettingsBundle>("/api/settings");
  const [tab, setTab] = useState<Tab>("Restaurant & rules");

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Settings" description="Changes apply from the next call." />
      <div role="tablist" aria-label="Settings sections" className="flex gap-1 overflow-x-auto border-b border-zinc-200 dark:border-zinc-800">
        {TABS.map((t) => (
          <button
            key={t}
            role="tab"
            type="button"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`-mb-px shrink-0 border-b-2 px-3 py-2 text-sm font-medium ${
              tab === t
                ? "border-emerald-600 text-emerald-700 dark:text-emerald-400"
                : "border-transparent text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-100"
            }`}
          >
            {t}
          </button>
        ))}
      </div>
      {error && <ErrorBanner>{error.message}</ErrorBanner>}
      {tab === "System" ? (
        <BackendStatus />
      ) : loading && !data ? (
        <Loading />
      ) : data ? (
        <>
          {tab === "Restaurant & rules" && <RestaurantForm key={JSON.stringify(data.restaurant)} initial={data.restaurant} onSaved={reload} />}
          {tab === "Opening hours" && <HoursForm key={JSON.stringify(data.hours)} initial={data.hours} onSaved={reload} />}
          {tab === "Tables" && <TablesEditor tables={data.tables} onChanged={reload} />}
          {tab === "Assistant" && <AssistantForm key={JSON.stringify(data.assistant)} initial={data.assistant} onSaved={reload} />}
        </>
      ) : null}
    </div>
  );
}

/** Submit + status line shared by the forms. */
function useSave() {
  const [state, setState] = useState<{ busy: boolean; error: string | null; saved: boolean }>({ busy: false, error: null, saved: false });
  const save = async (fn: () => Promise<unknown>, after: () => void) => {
    setState({ busy: true, error: null, saved: false });
    try {
      await fn();
      setState({ busy: false, error: null, saved: true });
      after();
    } catch (err) {
      setState({ busy: false, error: err instanceof ApiError ? err.message : "Could not save.", saved: false });
    }
  };
  const footer = (
    <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
      <Button type="submit" disabled={state.busy}>{state.busy ? "Saving…" : "Save"}</Button>
      {state.saved && <SuccessBanner>Saved.</SuccessBanner>}
      {state.error && <ErrorBanner>{state.error}</ErrorBanner>}
    </div>
  );
  return { save, footer };
}

function Grid({ children }: { children: ReactNode }) {
  return <div className="grid gap-4 sm:grid-cols-2">{children}</div>;
}

function RestaurantForm({ initial, onSaved }: { initial: RestaurantSettings; onSaved: () => void }) {
  const [f, setF] = useState(initial);
  const { save, footer } = useSave();
  const set = <K extends keyof RestaurantSettings>(k: K, v: RestaurantSettings[K]) => setF((x) => ({ ...x, [k]: v }));
  const num = (k: keyof RestaurantSettings) => (e: { target: { value: string } }) => set(k, Number(e.target.value) as never);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    // id and slug identify the restaurant; they are not editable.
    const body = Object.fromEntries(Object.entries(f).filter(([k]) => k !== "id" && k !== "slug"));
    void save(() => api("/api/settings/restaurant", { method: "PUT", json: body }), onSaved);
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-6">
      <Card title="Restaurant">
        <Grid>
          <Field label="Name"><TextInput required value={f.name} onChange={(e) => set("name", e.target.value)} /></Field>
          <Field label="Cuisine"><TextInput value={f.cuisine} onChange={(e) => set("cuisine", e.target.value)} /></Field>
          <Field label="Address"><TextInput value={f.address} onChange={(e) => set("address", e.target.value)} /></Field>
          <Field label="Phone"><TextInput value={f.phone} onChange={(e) => set("phone", e.target.value)} /></Field>
          <Field label="Email"><TextInput type="email" value={f.email} onChange={(e) => set("email", e.target.value)} /></Field>
          <Field label="Time zone" hint="IANA name, e.g. Asia/Karachi"><TextInput required value={f.timezone} onChange={(e) => set("timezone", e.target.value)} /></Field>
          <div className="sm:col-span-2">
            <Field label="Description"><TextInput value={f.description} onChange={(e) => set("description", e.target.value)} /></Field>
          </div>
          <Field label="Services" hint="One per line">
            <textarea className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-950" rows={5}
              value={f.services.join("\n")} onChange={(e) => set("services", e.target.value.split("\n").map((s) => s.trim()).filter(Boolean))} />
          </Field>
          <Field label="Policies" hint="One per line">
            <textarea className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-950" rows={5}
              value={f.policies.join("\n")} onChange={(e) => set("policies", e.target.value.split("\n").map((s) => s.trim()).filter(Boolean))} />
          </Field>
        </Grid>
      </Card>
      <Card title="Reservation rules">
        <Grid>
          <Field label="Smallest party"><TextInput type="number" min={1} max={30} value={f.min_party_size} onChange={num("min_party_size")} /></Field>
          <Field label="Largest party by phone" hint="Larger groups are told to email"><TextInput type="number" min={1} max={30} value={f.max_party_size} onChange={num("max_party_size")} /></Field>
          <Field label="Book up to (days ahead)"><TextInput type="number" min={1} max={365} value={f.booking_window_days} onChange={num("booking_window_days")} /></Field>
          <Field label="Minimum notice (minutes)"><TextInput type="number" min={0} max={1440} value={f.min_notice_minutes} onChange={num("min_notice_minutes")} /></Field>
          <Field label="Last seating (minutes before closing)"><TextInput type="number" min={0} max={600} value={f.last_seating_minutes_before_close} onChange={num("last_seating_minutes_before_close")} /></Field>
          <Field label="Table held for (minutes)" hint="How long a booking occupies its table"><TextInput type="number" min={15} max={480} value={f.dining_duration_minutes} onChange={num("dining_duration_minutes")} /></Field>
          <Field label="Booking time step (minutes)"><TextInput type="number" min={5} max={60} value={f.slot_interval_minutes} onChange={num("slot_interval_minutes")} /></Field>
          <Field label="Phone number">
            <Select value={f.require_phone ? "yes" : "no"} onChange={(e) => set("require_phone", e.target.value === "yes")}>
              <option value="no">Optional</option>
              <option value="yes">Required for every booking</option>
            </Select>
          </Field>
          {footer}
        </Grid>
      </Card>
    </form>
  );
}

function HoursForm({ initial, onSaved }: { initial: DayHours[]; onSaved: () => void }) {
  const [days, setDays] = useState(initial.map((d) => (d ? { opens: d.opens.slice(0, 5), closes: d.closes.slice(0, 5) } : null)));
  const { save, footer } = useSave();
  const update = (i: number, value: DayHours) => setDays((all) => all.map((d, j) => (j === i ? value : d)));

  return (
    <form onSubmit={(e) => { e.preventDefault(); void save(() => api("/api/settings/hours", { method: "PUT", json: { days } }), onSaved); }}>
      <Card title="Opening hours" actions={<span className="text-xs text-zinc-500 dark:text-zinc-400">Local time; closing must be before midnight</span>}>
        <div className="flex flex-col gap-3">
          {WEEKDAYS.map((name, i) => {
            const d = days[i];
            return (
              <div key={name} className="grid grid-cols-[7rem_auto_1fr_1fr] items-center gap-3 text-sm">
                <span className="font-medium">{name}</span>
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={d !== null} onChange={(e) => update(i, e.target.checked ? { opens: "12:00", closes: "22:00" } : null)} />
                  Open
                </label>
                {d ? (
                  <>
                    <TextInput type="time" aria-label={`${name} opens`} value={d.opens} onChange={(e) => update(i, { ...d, opens: e.target.value })} />
                    <TextInput type="time" aria-label={`${name} closes`} value={d.closes} onChange={(e) => update(i, { ...d, closes: e.target.value })} />
                  </>
                ) : (
                  <span className="col-span-2 text-zinc-500 dark:text-zinc-400">Closed</span>
                )}
              </div>
            );
          })}
          <div className="grid">{footer}</div>
        </div>
      </Card>
    </form>
  );
}

function TablesEditor({ tables, onChanged }: { tables: TableRow[]; onChanged: () => void }) {
  const [draft, setDraft] = useState({ label: "", seats: 4, min_party_size: 1, area: "main" });
  const [error, setError] = useState<string | null>(null);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save.");
    }
  };
  const seats = tables.filter((t) => t.is_active).reduce((sum, t) => sum + t.seats, 0);

  return (
    <Card title="Tables" actions={<span className="text-xs text-zinc-500 dark:text-zinc-400">{tables.filter((t) => t.is_active).length} active · {seats} seats</span>}>
      {error && <div className="mb-4"><ErrorBanner>{error}</ErrorBanner></div>}
      <div className="-mx-5 overflow-x-auto">
        <table className="w-full min-w-[560px] text-sm" aria-label="Tables">
          <thead>
            <tr className="border-b border-zinc-200 text-left text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
              <th className="px-5 py-2 font-medium">Table</th>
              <th className="px-3 py-2 text-right font-medium">Seats</th>
              <th className="px-3 py-2 text-right font-medium">Min party</th>
              <th className="px-3 py-2 font-medium">Area</th>
              <th className="px-3 py-2 font-medium">Status</th>
              <th className="px-5 py-2"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {tables.map((t) => (
              <tr key={t.id} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800">
                <td className="px-5 py-2 font-medium">{t.label}</td>
                <td className="px-3 py-2 text-right tabular-nums">{t.seats}</td>
                <td className="px-3 py-2 text-right tabular-nums">{t.min_party_size}</td>
                <td className="px-3 py-2">{t.area}</td>
                <td className="px-3 py-2">{t.is_active ? <Badge tone="good">Bookable</Badge> : <Badge tone="neutral">Out of use</Badge>}</td>
                <td className="px-5 py-2 text-right">
                  <Button variant="secondary" onClick={() => run(() => api(`/api/settings/tables/${t.id}`, { method: "PATCH", json: { is_active: !t.is_active } }))}>
                    {t.is_active ? "Take out of use" : "Make bookable"}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <form
        className="mt-5 grid items-end gap-3 sm:grid-cols-5"
        onSubmit={(e) => {
          e.preventDefault();
          void run(() => api("/api/settings/tables", { method: "POST", json: draft })).then(() => setDraft((d) => ({ ...d, label: "" })));
        }}
      >
        <Field label="New table"><TextInput required maxLength={20} placeholder="T13" value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })} /></Field>
        <Field label="Seats"><TextInput type="number" min={1} max={30} value={draft.seats} onChange={(e) => setDraft({ ...draft, seats: Number(e.target.value) })} /></Field>
        <Field label="Min party"><TextInput type="number" min={1} max={30} value={draft.min_party_size} onChange={(e) => setDraft({ ...draft, min_party_size: Number(e.target.value) })} /></Field>
        <Field label="Area"><TextInput required maxLength={40} value={draft.area} onChange={(e) => setDraft({ ...draft, area: e.target.value })} /></Field>
        <Button type="submit">Add table</Button>
      </form>
    </Card>
  );
}

function AssistantForm({ initial, onSaved }: { initial: AssistantSettings; onSaved: () => void }) {
  const [f, setF] = useState(initial);
  const { save, footer } = useSave();
  return (
    <form onSubmit={(e) => { e.preventDefault(); void save(() => api("/api/settings/assistant", { method: "PUT", json: f }), onSaved); }}>
      <Card title="AI personality">
        <Grid>
          <Field label="Assistant name"><TextInput required maxLength={40} value={f.assistant_name} onChange={(e) => setF({ ...f, assistant_name: e.target.value })} /></Field>
          <Field label="Voice ID" hint="An ElevenLabs premade voice ID; empty = default (Sarah)">
            <TextInput maxLength={40} value={f.voice_id ?? ""} onChange={(e) => setF({ ...f, voice_id: e.target.value })} />
          </Field>
          <div className="sm:col-span-2">
            <Field label="Personality" hint='How the assistant should come across, e.g. "warm, professional and concise"'>
              <TextInput required maxLength={300} value={f.personality} onChange={(e) => setF({ ...f, personality: e.target.value })} />
            </Field>
          </div>
          <div className="sm:col-span-2">
            <Field label="Greeting" hint="The first sentence callers hear. Empty = built from the restaurant and assistant names.">
              <TextInput maxLength={300} value={f.greeting ?? ""} onChange={(e) => setF({ ...f, greeting: e.target.value })} />
            </Field>
          </div>
          {footer}
        </Grid>
      </Card>
    </form>
  );
}
