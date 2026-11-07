"use client";

import Link from "next/link";

import { BarChart } from "@/components/dashboard/bar-chart";
import { Badge, Card, Empty, ErrorBanner, Loading, PageHeader, ReservationStatusBadge, StatTile } from "@/components/ui";
import { formatDay, formatDuration, formatEndReason, formatInstant, formatPercent, formatTime } from "@/lib/format";
import type { Dashboard } from "@/lib/types";
import { useApi } from "@/lib/use-api";

export function DashboardView() {
  const { data, error, loading } = useApi<Dashboard>("/api/dashboard", 15_000);

  if (loading && !data) return <Loading />;
  if (!data) return <ErrorBanner>{error?.message ?? "Could not load the dashboard."}</ErrorBanner>;
  const s = data.stats;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Dashboard"
        description={`${data.restaurant_name} · today is ${formatDay(data.today)}`}
        actions={
          <Link href="/call" className="rounded-lg bg-emerald-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-emerald-700">
            Start a test call
          </Link>
        }
      />
      {error && <ErrorBanner>{error.message} (showing the last data)</ErrorBanner>}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile label="Today's reservations" value={s.today_reservations} hint={`${s.today_guests} guests expected`} />
        <StatTile label="Total reservations" value={s.total_reservations} hint={`${s.upcoming_reservations} upcoming · ${s.cancelled_reservations} cancelled`} />
        <StatTile label="Voice calls" value={s.total_calls} hint={`avg ${formatDuration(s.avg_call_seconds)}`} />
        <StatTile label="Booked by voice" value={s.successful_bookings} hint={`${formatPercent(s.booking_rate)} of calls`} />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Bookings, next 7 days">
          <BarChart
            unit="bookings"
            bars={data.bookings_next_7_days.map((d) => ({
              key: d.date,
              label: d.weekday,
              value: d.bookings,
              detail: `${d.guests} guests`,
            }))}
          />
        </Card>
        <Card title="Calls, last 7 days">
          <BarChart
            unit="calls"
            bars={data.calls_last_7_days.map((d) => ({ key: d.date, label: d.weekday, value: d.calls }))}
          />
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card
          title="Recent conversations"
          actions={<Link href="/calls" className="text-xs font-medium text-emerald-700 hover:underline dark:text-emerald-400">All calls</Link>}
        >
          {data.recent_calls.length === 0 ? (
            <Empty>No calls yet. Try the live call.</Empty>
          ) : (
            <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {data.recent_calls.map((c) => (
                <li key={c.id} className="flex items-start justify-between gap-3 py-3 text-sm">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{c.first_request ?? "(no speech)"}</p>
                    <p className="text-xs text-zinc-500 dark:text-zinc-400">
                      {formatInstant(c.started_at)} · {formatDuration(c.duration_seconds)} · {formatEndReason(c.end_reason)}
                    </p>
                  </div>
                  {c.booking_reference ? (
                    <Badge tone="good">Booked {c.booking_reference}</Badge>
                  ) : c.status === "error" ? (
                    <Badge tone="critical">Error</Badge>
                  ) : (
                    <Badge tone="neutral">No booking</Badge>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          title="Recent reservation activity"
          actions={<Link href="/reservations" className="text-xs font-medium text-emerald-700 hover:underline dark:text-emerald-400">All reservations</Link>}
        >
          {data.recent_activity.length === 0 ? (
            <Empty>No reservations yet.</Empty>
          ) : (
            <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {data.recent_activity.map((r) => (
                <li key={r.id} className="flex items-start justify-between gap-3 py-3 text-sm">
                  <div className="min-w-0">
                    <p className="truncate font-medium">
                      {r.customer_name} · {r.guests} {r.guests === 1 ? "guest" : "guests"}
                    </p>
                    <p className="text-xs text-zinc-500 dark:text-zinc-400">
                      {formatDay(r.date, r.weekday)}, {formatTime(r.time)} · {r.booking_reference} · via {r.source}
                    </p>
                  </div>
                  <ReservationStatusBadge status={r.status} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
