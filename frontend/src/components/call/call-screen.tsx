"use client";

import dynamic from "next/dynamic";

// The Pipecat client and WebRTC transport only exist in the browser,
// so the call UI is loaded client-side only.
const VoiceCall = dynamic(() => import("@/components/call/voice-call"), {
  ssr: false,
  loading: () => (
    <div className="h-[34rem] w-full animate-pulse rounded-3xl bg-zinc-100 dark:bg-zinc-900" />
  ),
});

export function CallScreen() {
  return <VoiceCall />;
}
