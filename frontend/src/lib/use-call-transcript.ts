"use client";

import { type BotOutputData, RTVIEvent } from "@pipecat-ai/client-js";
import { useRTVIClientEvent } from "@pipecat-ai/client-react";
import { useCallback, useState } from "react";

/** One sentence of an assistant reply: the words already spoken, and the ones still to come. */
export type SpokenSegment = { id: number | string; spoken: string; pending: string };

export type TranscriptLine = {
  id: string;
  role: "user" | "assistant";
  /** Caller: recognised words. Assistant: the words already spoken. */
  text: string;
  /** Caller lines only: interim lines are replaced as Deepgram refines them. */
  final: boolean;
  /** Assistant lines only: the reply's sentences, in order. */
  segments?: SpokenSegment[];
  /** Assistant lines only: the caller cut in, so the unspoken rest was never said. */
  interrupted?: boolean;
};

/** One `bot-output` message as a segment update. */
function toSegment(data: BotOutputData, fallbackId: number): SpokenSegment {
  const id = data.segment_id ?? fallbackId;
  const progress = data.spoken_progress;
  if (data.will_be_spoken === false || data.spoken_status === undefined) {
    return { id, spoken: data.text, pending: "" };
  }
  if (data.spoken_status === "new" || !progress) {
    return { id, spoken: "", pending: data.text };
  }
  return { id, spoken: progress.accumulated_text, pending: progress.remaining_text };
}

function spokenText(segments: SpokenSegment[]): string {
  return segments
    .map((s) => s.spoken.trim())
    .filter(Boolean)
    .join(" ");
}

/**
 * Live call transcript.
 *
 * Caller lines come from RTVI `user-input` messages: Deepgram sends interim
 * results for the current utterance (each replaces the previous one), then a
 * final result that fixes the line.
 *
 * Assistant lines come from `bot-output` messages, which follow the voice: a
 * segment (sentence) arrives as `new` when it is about to be spoken, then
 * `in-progress` / `completed` as ElevenLabs' word timestamps pass. Segments keep
 * their order; spoken words are shown solid and upcoming ones faded. On
 * `bot-interrupted` the unspoken words are dropped, so the transcript shows
 * exactly what the caller heard.
 */
export function useCallTranscript() {
  const [lines, setLines] = useState<TranscriptLine[]>([]);

  useRTVIClientEvent(RTVIEvent.BotOutput, (data: BotOutputData) => {
    if (!data.text?.trim()) return;
    setLines((prev) => {
      const last = prev.at(-1);
      const current: TranscriptLine =
        last?.role === "assistant" && !last.interrupted
          ? last
          : { id: crypto.randomUUID(), role: "assistant", text: "", final: true, segments: [] };
      const segments = [...(current.segments ?? [])];
      const update = toSegment(data, segments.length);
      const index = segments.findIndex((s) => s.id === update.id);
      if (index >= 0) segments[index] = update;
      else segments.push(update);
      const line: TranscriptLine = { ...current, segments, text: spokenText(segments) };
      return current === last ? [...prev.slice(0, -1), line] : [...prev, line];
    });
  });

  useRTVIClientEvent(RTVIEvent.BotInterrupted, () => {
    setLines((prev) => {
      const index = prev.findLastIndex((l) => l.role === "assistant");
      const line = prev[index];
      if (index < 0 || !line.segments?.some((s) => s.pending)) return prev;
      const segments = line.segments
        .map((s) => ({ ...s, pending: "" }))
        .filter((s) => s.spoken.trim());
      const next = [...prev];
      next[index] = { ...line, segments, interrupted: true };
      return next;
    });
  });

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
