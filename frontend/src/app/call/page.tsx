import type { Metadata } from "next";
import Link from "next/link";

import { CallScreen } from "@/components/call/call-screen";

export const metadata: Metadata = {
  title: "Live Call · Restaurant Voice Assistant",
};

export default function CallPage() {
  return (
    <main className="mx-auto flex w-full max-w-xl flex-1 flex-col gap-6 px-4 py-10 sm:px-6">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Live call</h1>
        <Link href="/" className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
          ← Home
        </Link>
      </header>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        <strong>Milestone 2: live transcription.</strong> Start a call and speak. Your words appear
        in real time. The assistant starts answering in Milestone 3.
      </p>
      <CallScreen />
    </main>
  );
}
