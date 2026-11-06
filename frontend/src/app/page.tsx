import { BackendStatus } from "@/components/backend-status";

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 px-4 py-16 sm:px-6">
      <header>
        <p className="text-sm font-medium uppercase tracking-wide text-emerald-600 dark:text-emerald-400">
          AI Receptionist
        </p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">
          Restaurant Reservation Voice Assistant
        </h1>
        <p className="mt-3 text-zinc-600 dark:text-zinc-400">
          A real-time voice receptionist that answers questions, checks availability, and books
          tables. The live call screen and dashboard arrive in later milestones.
        </p>
      </header>
      <BackendStatus />
    </main>
  );
}
