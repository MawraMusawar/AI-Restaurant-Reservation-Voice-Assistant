"use client";

import { PipecatClient, RTVIEvent, type TransportState } from "@pipecat-ai/client-js";
import {
  PipecatClientAudio,
  PipecatClientProvider,
  usePipecatClient,
  usePipecatClientMediaTrack,
  usePipecatClientMicControl,
  usePipecatClientTransportState,
  useRTVIClientEvent,
} from "@pipecat-ai/client-react";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";
import { useCallback, useEffect, useState } from "react";

import { AudioLevelMeter } from "@/components/call/audio-level-meter";
import { TranscriptPanel } from "@/components/call/transcript-panel";
import {
  describeAssistantError,
  describeToolActivity,
  isAssistantError,
} from "@/lib/assistant-messages";
import { useCallTranscript } from "@/lib/use-call-transcript";
import { useTrackAudioLevel } from "@/lib/use-track-audio-level";
import { describeConnectError, describeDeviceError } from "@/lib/voice-errors";
import { describeServerError, fetchVoiceStatus } from "@/lib/voice-status";

/** Same-origin signaling endpoint; Next.js proxies it to FastAPI. */
const OFFER_ENDPOINT = "/api/offer";

/**
 * Owns the PipecatClient for the call screen.
 * Rendered client-side only (see call-screen.tsx), so the browser-only
 * transport is never constructed during server rendering.
 */
export default function VoiceCall() {
  const [client] = useState(
    () =>
      new PipecatClient({
        transport: new SmallWebRTCTransport(),
        enableMic: true,
        enableCam: false,
      }),
  );

  // Hang up if the user navigates away mid-call.
  useEffect(() => {
    return () => {
      void client.disconnect();
    };
  }, [client]);

  return (
    <PipecatClientProvider client={client}>
      <CallPanel />
      <PipecatClientAudio />
    </PipecatClientProvider>
  );
}

const CONNECTING_STATES: TransportState[] = [
  "initializing",
  "initialized",
  "authenticating",
  "authenticated",
  "connecting",
  "connected",
];

function statusLabel(state: TransportState): string {
  if (state === "ready") return "On call";
  if (state === "connected") return "Connected, waiting for the assistant…";
  if (CONNECTING_STATES.includes(state)) return "Connecting…";
  if (state === "disconnecting") return "Ending call…";
  if (state === "error") return "Connection error";
  return "Ready to call";
}

function formatDuration(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function CallPanel() {
  const client = usePipecatClient();
  const transportState = usePipecatClientTransportState();
  const { enableMic, isMicEnabled } = usePipecatClientMicControl();

  const [error, setError] = useState<string | null>(null);
  const [userSpeaking, setUserSpeaking] = useState(false);
  const [botSpeaking, setBotSpeaking] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [toolActivity, setToolActivity] = useState<string | null>(null);
  // Non-fatal problems (e.g. LLM rate limit): the call continues.
  const [notice, setNotice] = useState<string | null>(null);
  const [localLevel, setLocalLevel] = useState(0);
  // RemoteAudioLevel never fires with SmallWebRTC, so measure the bot's track directly.
  const botAudioTrack = usePipecatClientMediaTrack("audio", "bot");
  const remoteLevel = useTrackAudioLevel(botAudioTrack);
  const [callStartedAt, setCallStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const inCall = transportState === "ready";
  const isBusy = CONNECTING_STATES.includes(transportState) || transportState === "disconnecting";
  const canStart = !isBusy && !inCall;

  const { lines: transcript, reset: resetTranscript } = useCallTranscript();

  useRTVIClientEvent(RTVIEvent.BotReady, () => {
    resetTranscript();
    setCallStartedAt(Date.now());
  });
  useRTVIClientEvent(RTVIEvent.Disconnected, () => {
    setCallStartedAt(null);
    setUserSpeaking(false);
    setBotSpeaking(false);
    setThinking(false);
    setToolActivity(null);
    setNotice(null);
    setLocalLevel(0);
  });
  useRTVIClientEvent(RTVIEvent.UserStartedSpeaking, () => setUserSpeaking(true));
  useRTVIClientEvent(RTVIEvent.UserStoppedSpeaking, () => setUserSpeaking(false));
  useRTVIClientEvent(RTVIEvent.BotStartedSpeaking, () => {
    setBotSpeaking(true);
    setNotice(null); // the assistant is answering again
  });
  useRTVIClientEvent(RTVIEvent.BotStoppedSpeaking, () => setBotSpeaking(false));
  useRTVIClientEvent(RTVIEvent.LocalAudioLevel, (level) => setLocalLevel(level));
  useRTVIClientEvent(RTVIEvent.DeviceError, (deviceError) =>
    setError(describeDeviceError(deviceError)),
  );
  // What the assistant is doing between the caller's turn and its reply.
  useRTVIClientEvent(RTVIEvent.BotLlmStarted, () => setThinking(true));
  useRTVIClientEvent(RTVIEvent.BotLlmStopped, () => setThinking(false));
  useRTVIClientEvent(RTVIEvent.LLMFunctionCallStarted, (data) =>
    setToolActivity(describeToolActivity(data.function_name)),
  );
  useRTVIClientEvent(RTVIEvent.LLMFunctionCallStopped, () => setToolActivity(null));
  useRTVIClientEvent(RTVIEvent.ServerMessage, (data: unknown) => {
    if (isAssistantError(data)) setNotice(describeAssistantError(data));
  });
  useRTVIClientEvent(RTVIEvent.Error, (message) => {
    const data = message.data as { message?: string } | undefined;
    console.warn("Voice pipeline error:", data?.message);
    setError(describeServerError(data?.message));
  });

  // Tick the call timer once per second while a call is active.
  useEffect(() => {
    if (callStartedAt === null) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [callStartedAt]);

  const startCall = useCallback(async () => {
    if (!client) return;
    setError(null);
    try {
      // Fail fast with a clear reason (missing API key, all lines busy) instead of
      // a failed WebRTC negotiation.
      const status = await fetchVoiceStatus();
      if (!status.ready) {
        setError(status.detail ?? "The voice assistant is not available right now.");
        return;
      }
      await client.connect({ webrtcRequestParams: { endpoint: OFFER_ENDPOINT } });
    } catch (err) {
      setError(await describeConnectError(err));
      void client.disconnect();
    }
  }, [client]);

  const endCall = useCallback(async () => {
    await client?.disconnect();
  }, [client]);

  const assistantActivity = botSpeaking
    ? "Speaking"
    : (toolActivity ?? (thinking ? "Thinking…" : "Listening"));
  const elapsed = callStartedAt === null ? 0 : Math.max(0, Math.floor((now - callStartedAt) / 1000));

  return (
    <section className="w-full rounded-3xl border border-zinc-200 bg-white p-6 shadow-sm sm:p-8 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex flex-col items-center text-center">
        <div
          className={`flex h-24 w-24 items-center justify-center rounded-full text-3xl transition-shadow ${
            botSpeaking
              ? "bg-emerald-100 shadow-[0_0_0_10px_rgba(16,185,129,0.15)] dark:bg-emerald-950"
              : "bg-zinc-100 dark:bg-zinc-800"
          }`}
          aria-hidden
        >
          🍽️
        </div>
        <h2 className="mt-4 text-xl font-semibold">Restaurant Receptionist</h2>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400" role="status" aria-live="polite">
          {statusLabel(transportState)}
        </p>
        <p className="mt-2 font-mono text-2xl tabular-nums" aria-label="Call duration">
          {formatDuration(elapsed)}
        </p>
      </div>

      <div className="mt-8 grid grid-cols-2 gap-3">
        <Indicator label="You" detail={userSpeaking ? "Speaking" : "Listening"} active={inCall && userSpeaking} tone="caller" />
        <Indicator
          label="Assistant"
          detail={assistantActivity}
          active={inCall && (botSpeaking || thinking || toolActivity !== null)}
          tone="agent"
        />
      </div>

      <div className="mt-6 flex justify-between gap-6 rounded-2xl bg-zinc-50 px-5 py-4 dark:bg-zinc-950">
        <AudioLevelMeter label="Your microphone" level={localLevel} active={inCall && isMicEnabled} tone="caller" />
        <AudioLevelMeter label="Assistant audio" level={remoteLevel} active={inCall} tone="agent" />
      </div>

      <TranscriptPanel lines={transcript} inCall={inCall} />

      <dl className="mt-6 grid grid-cols-2 gap-3 text-sm">
        <StatusItem label="Connection" value={transportState} />
        <StatusItem label="Microphone" value={isMicEnabled ? "On" : "Muted"} />
      </dl>

      {notice && (
        <p role="status" className="mt-6 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          {notice}
        </p>
      )}

      {error && (
        <p role="alert" className="mt-6 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
          {error}
        </p>
      )}

      <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
        {inCall || isBusy ? (
          <>
            <button
              type="button"
              onClick={() => enableMic(!isMicEnabled)}
              disabled={!inCall}
              className="rounded-full border border-zinc-300 px-5 py-3 text-sm font-medium transition hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
            >
              {isMicEnabled ? "Mute" : "Unmute"}
            </button>
            <button
              type="button"
              onClick={endCall}
              disabled={transportState === "disconnecting"}
              className="rounded-full bg-red-600 px-8 py-3 text-sm font-semibold text-white transition hover:bg-red-700 disabled:opacity-60"
            >
              End call
            </button>
          </>
        ) : (
          <button
            type="button"
            onClick={startCall}
            disabled={!canStart}
            className="rounded-full bg-emerald-600 px-8 py-3 text-sm font-semibold text-white transition hover:bg-emerald-700 disabled:opacity-60"
          >
            Start call
          </button>
        )}
      </div>
    </section>
  );
}

function Indicator({
  label,
  detail,
  active,
  tone,
}: {
  label: string;
  detail: string;
  active: boolean;
  tone: "caller" | "agent";
}) {
  const activeStyles =
    tone === "caller"
      ? "border-sky-300 bg-sky-50 dark:border-sky-800 dark:bg-sky-950"
      : "border-emerald-300 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950";
  const dot = tone === "caller" ? "bg-sky-500" : "bg-emerald-500";
  return (
    <div
      className={`flex items-center gap-3 rounded-2xl border px-4 py-3 transition-colors ${
        active ? activeStyles : "border-zinc-200 dark:border-zinc-800"
      }`}
    >
      <span className={`h-2.5 w-2.5 rounded-full ${active ? `${dot} animate-pulse` : "bg-zinc-300 dark:bg-zinc-700"}`} />
      <div className="text-left">
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-zinc-500 dark:text-zinc-400">{detail}</p>
      </div>
    </div>
  );
}

function StatusItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-zinc-200 px-4 py-3 dark:border-zinc-800">
      <dt className="text-xs text-zinc-500 dark:text-zinc-400">{label}</dt>
      <dd className="mt-0.5 font-medium capitalize">{value}</dd>
    </div>
  );
}
