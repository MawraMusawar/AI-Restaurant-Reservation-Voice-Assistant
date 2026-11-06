"use client";

import { useEffect, useState } from "react";

type HealthResponse = {
  status: "ok";
  version: string;
  environment: string;
  integrations: Record<string, boolean>;
};

type StatusState =
  | { kind: "loading" }
  | { kind: "ok"; data: HealthResponse }
  | { kind: "error"; message: string };

const INTEGRATION_LABELS: Record<string, string> = {
  deepgram: "Deepgram STT",
  groq: "Groq LLM",
  elevenlabs: "ElevenLabs TTS",
  database: "Supabase database",
};

export function BackendStatus() {
  const [state, setState] = useState<StatusState>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/health", { signal: controller.signal, cache: "no-store" })
      .then(async (res) => {
        if (!res.ok) throw new Error(`Backend responded with HTTP ${res.status}`);
        return (await res.json()) as HealthResponse;
      })
      .then((data) => setState({ kind: "ok", data }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "Unknown error",
        });
      });
    return () => controller.abort();
  }, []);

  return (
    <section className="w-full rounded-2xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-lg font-semibold">System status</h2>
        <StatusBadge state={state} />
      </div>

      {state.kind === "error" && (
        <p className="mt-4 text-sm text-zinc-600 dark:text-zinc-400">
          Could not reach the backend ({state.message}). Start it with{" "}
          <code className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-xs dark:bg-zinc-800">
            uv run uvicorn app.main:app --reload --port 8000
          </code>{" "}
          in the <code className="font-mono text-xs">backend/</code> folder.
        </p>
      )}

      {state.kind === "ok" && (
        <>
          <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
            Backend v{state.data.version} · {state.data.environment}
          </p>
          <ul className="mt-5 grid gap-3 sm:grid-cols-2">
            {Object.entries(state.data.integrations).map(([key, configured]) => (
              <li
                key={key}
                className="flex items-center justify-between rounded-xl border border-zinc-200 px-4 py-3 text-sm dark:border-zinc-800"
              >
                <span>{INTEGRATION_LABELS[key] ?? key}</span>
                <span
                  className={
                    configured
                      ? "font-medium text-emerald-600 dark:text-emerald-400"
                      : "text-zinc-400 dark:text-zinc-500"
                  }
                >
                  {configured ? "Configured" : "Not configured"}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function StatusBadge({ state }: { state: StatusState }) {
  const styles = {
    loading: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
    ok: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
    error: "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300",
  }[state.kind];
  const label = { loading: "Checking…", ok: "Backend online", error: "Backend offline" }[
    state.kind
  ];
  return (
    <span role="status" className={`rounded-full px-3 py-1 text-xs font-medium ${styles}`}>
      {label}
    </span>
  );
}
