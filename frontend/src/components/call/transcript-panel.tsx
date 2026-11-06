"use client";

import { useEffect, useRef } from "react";

import type { TranscriptLine } from "@/lib/use-call-transcript";

type TranscriptPanelProps = {
  lines: TranscriptLine[];
  inCall: boolean;
};

export function TranscriptPanel({ lines, inCall }: TranscriptPanelProps) {
  const endRef = useRef<HTMLDivElement>(null);

  // Keep the newest words in view.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [lines]);

  return (
    <section
      aria-label="Live transcript"
      className="mt-6 rounded-2xl border border-zinc-200 dark:border-zinc-800"
    >
      <h3 className="border-b border-zinc-200 px-5 py-3 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
        Live transcript
      </h3>
      <div className="max-h-64 min-h-24 overflow-y-auto px-5 py-4" aria-live="polite">
        {lines.length === 0 ? (
          <p className="text-sm text-zinc-400 dark:text-zinc-500">
            {inCall ? "Start speaking. Your words will appear here." : "The conversation will appear here."}
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {lines.map((line) => (
              <li key={line.id} data-role={line.role} data-final={line.final} className="text-sm">
                <span
                  className={`mr-2 text-xs font-semibold uppercase ${
                    line.role === "user" ? "text-sky-600 dark:text-sky-400" : "text-emerald-600 dark:text-emerald-400"
                  }`}
                >
                  {line.role === "user" ? "You" : "Assistant"}
                </span>
                {line.segments ? (
                  line.segments.map((segment, i) => (
                    <span key={segment.id}>
                      {i > 0 && " "}
                      {segment.spoken}
                      {segment.pending && (
                        <span className="text-zinc-400 dark:text-zinc-500">
                          {segment.spoken ? " " : ""}
                          {segment.pending}
                        </span>
                      )}
                    </span>
                  ))
                ) : (
                  <span className={line.final ? "" : "italic text-zinc-500 dark:text-zinc-400"}>
                    {line.text}
                    {!line.final && "…"}
                  </span>
                )}
                {line.interrupted && (
                  <span className="ml-2 rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium uppercase text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
                    interrupted
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
        <div ref={endRef} />
      </div>
    </section>
  );
}
