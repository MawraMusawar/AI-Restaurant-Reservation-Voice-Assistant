"use client";

import { useId, useState } from "react";

export type Bar = { key: string; label: string; value: number; detail?: string };

/**
 * A single-series column chart (dataviz method): one validated hue (palette slot 1,
 * #2a78d6 light / #3987e5 dark, checked against the card surfaces), columns capped
 * at 24px with a 4px rounded data end and a square baseline, a recessive grid, a
 * hover/focus tooltip per column, and a table view for screen readers and exact values.
 * One series, so no legend: the card title names it.
 */
export function BarChart({ bars, unit, height = 160 }: { bars: Bar[]; unit: string; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);
  const titleId = useId();
  const max = Math.max(1, ...bars.map((b) => b.value));
  const ticks = niceTicks(max);
  const top = ticks.at(-1) ?? max;
  const width = 100 / bars.length;

  if (showTable) {
    return (
      <div>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-zinc-500 dark:text-zinc-400">
              <th className="py-1 font-medium">Day</th>
              <th className="py-1 text-right font-medium">{unit}</th>
            </tr>
          </thead>
          <tbody>
            {bars.map((b) => (
              <tr key={b.key} className="border-t border-zinc-100 dark:border-zinc-800">
                <td className="py-1">{b.label}</td>
                <td className="py-1 text-right tabular-nums">
                  {b.value}
                  {b.detail && <span className="ml-2 text-zinc-500 dark:text-zinc-400">({b.detail})</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <ToggleView showTable onToggle={() => setShowTable(false)} />
      </div>
    );
  }

  return (
    <div className="viz-root">
      <div className="relative" style={{ height }} role="img" aria-labelledby={titleId}>
        <span id={titleId} className="sr-only">
          {bars.map((b) => `${b.label}: ${b.value} ${unit}`).join(", ")}
        </span>
        {/* Recessive grid with value ticks */}
        {ticks.map((t) => (
          <div
            key={t}
            className="absolute right-0 left-6 border-t border-zinc-100 dark:border-zinc-800"
            style={{ bottom: `${(t / top) * 100}%` }}
          >
            <span className="absolute -top-2 -left-6 w-5 text-right text-[10px] tabular-nums text-zinc-400 dark:text-zinc-500">
              {t}
            </span>
          </div>
        ))}
        <div className="absolute inset-y-0 right-0 left-6 flex items-end">
          {bars.map((b, i) => (
            <button
              key={b.key}
              type="button"
              className="group relative flex h-full items-end justify-center outline-none"
              style={{ width: `${width}%` }}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
              aria-label={`${b.label}: ${b.value} ${unit}${b.detail ? `, ${b.detail}` : ""}`}
            >
              <span
                className="viz-bar block w-full max-w-6 rounded-t-[4px] transition-opacity group-focus-visible:ring-2 group-focus-visible:ring-emerald-500"
                style={{
                  height: b.value ? `${(b.value / top) * 100}%` : "2px",
                  opacity: hover === null || hover === i ? 1 : 0.55,
                }}
              />
              {hover === i && (
                <span className="pointer-events-none absolute bottom-full z-10 mb-1 rounded-md bg-zinc-900 px-2 py-1 text-xs whitespace-nowrap text-white shadow dark:bg-zinc-100 dark:text-zinc-900">
                  <span className="font-semibold tabular-nums">
                    {b.value} {unit}
                  </span>
                  {b.detail && <span className="ml-1 opacity-80">· {b.detail}</span>}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-2 ml-6 flex">
        {bars.map((b) => (
          <span key={b.key} className="text-center text-xs text-zinc-500 dark:text-zinc-400" style={{ width: `${width}%` }}>
            {b.label}
          </span>
        ))}
      </div>
      <ToggleView showTable={false} onToggle={() => setShowTable(true)} />
    </div>
  );
}

function ToggleView({ showTable, onToggle }: { showTable: boolean; onToggle: () => void }) {
  return (
    <button type="button" onClick={onToggle} className="mt-3 text-xs text-zinc-500 underline-offset-2 hover:underline dark:text-zinc-400">
      {showTable ? "Show chart" : "Show as table"}
    </button>
  );
}

/** 0 plus 2-4 round ticks covering max. */
function niceTicks(max: number): number[] {
  const step = max <= 4 ? 1 : max <= 10 ? 2 : max <= 20 ? 5 : Math.ceil(max / 4 / 5) * 5;
  const ticks: number[] = [];
  for (let t = 0; t <= max + step - 1; t += step) ticks.push(t);
  if (ticks.at(-1)! < max) ticks.push(ticks.at(-1)! + step);
  return ticks;
}
