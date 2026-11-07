"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Button, ErrorBanner, Field, TextInput } from "@/components/ui";
import { ApiError, api } from "@/lib/api";

export function LoginForm() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/api/auth/login", { method: "POST", json: { password } });
      router.replace("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign in failed.");
      setBusy(false);
    }
  };

  return (
    <form
      onSubmit={submit}
      className="w-full max-w-sm space-y-5 rounded-2xl border border-zinc-200 bg-white p-8 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
    >
      <div>
        <h1 className="text-xl font-semibold">Staff sign in</h1>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
          Dashboard, reservations, call logs and settings.
        </p>
      </div>
      <Field label="Admin password">
        <TextInput
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoFocus
        />
      </Field>
      {error && <ErrorBanner>{error}</ErrorBanner>}
      <Button type="submit" disabled={busy || !password} className="w-full">
        {busy ? "Signing in…" : "Sign in"}
      </Button>
      <p className="text-center text-sm text-zinc-500 dark:text-zinc-400">
        Calling as a guest?{" "}
        <Link href="/call" className="font-medium text-emerald-700 hover:underline dark:text-emerald-400">
          Open the live call
        </Link>
      </p>
    </form>
  );
}
