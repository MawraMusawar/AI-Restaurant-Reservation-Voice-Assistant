"use client";

import { useRouter } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";

import { ErrorBanner, Loading } from "@/components/ui";
import { ApiError, api } from "@/lib/api";

export type AuthStatus = { signed_in: boolean; configured: boolean };

type GateState = { kind: "checking" } | { kind: "ok" } | { kind: "error"; message: string };

const NOT_CONFIGURED =
  "Staff screens are locked: set ADMIN_PASSWORD in backend/.env and restart the backend.";

/** Renders staff-only screens once the admin session is confirmed; else goes to /login. */
export function AdminGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<GateState>({ kind: "checking" });

  useEffect(() => {
    let cancelled = false;
    api<AuthStatus>("/api/auth/me")
      .then((me) => {
        if (cancelled) return;
        if (me.signed_in) setState({ kind: "ok" });
        else if (!me.configured) setState({ kind: "error", message: NOT_CONFIGURED });
        else router.replace("/login");
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", message: err instanceof ApiError ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (state.kind === "checking") return <Loading />;
  if (state.kind === "error") return <ErrorBanner>{state.message}</ErrorBanner>;
  return <>{children}</>;
}
