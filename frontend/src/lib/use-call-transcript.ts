"use client";

import { RTVIEvent } from "@pipecat-ai/client-js";
import { useRTVIClientEvent } from "@pipecat-ai/client-react";
import { useCallback, useState } from "react";

export type TranscriptLine = {
  id: string;
  role: "user" | "assistant";
  text: string;
  /** Interim lines are still being recognised and get replaced as words arrive. */
  final: boolean;
};

/**
 * Live call transcript built from RTVI `user-input` messages.
 *
 * Deepgram sends interim results for the current utterance (each one replaces the
 * previous one), then a final result that fixes the line. The next interim
 * result starts a new line. Assistant lines are added in Milestone 3.
 */
export function useCallTranscript() {
  const [lines, setLines] = useState<TranscriptLine[]>([]);

  useRTVIClientEvent(RTVIEvent.UserInput, (data) => {
    if (data.input_type !== "transcription") return;
    const text = data.text.trim();
    if (!text) return;
    setLines((prev) => {
      const last = prev.at(-1);
      const replacesInterim = last !== undefined && last.role === "user" && !last.final;
      const line: TranscriptLine = {
        id: replacesInterim ? last.id : crypto.randomUUID(),
        role: "user",
        text,
        final: data.final,
      };
      return replacesInterim ? [...prev.slice(0, -1), line] : [...prev, line];
    });
  });

  const reset = useCallback(() => setLines([]), []);
  return { lines, reset };
}
