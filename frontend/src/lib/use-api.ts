"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";

type State<T> = { data: T | null; error: ApiError | null; loading: boolean };

/**
 * Fetch JSON from the backend on mount, whenever `path` changes, on `reload()`, and
 * every `refreshMs` if given. A 401 sends the user to the login page; other errors are
 * returned for display (the last good data is kept).
 */
export function useApi<T>(path: string | null, refreshMs?: number) {
  const router = useRouter();
  const [state, setState] = useState<State<T>>({ data: null, error: null, loading: true });
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    const load = async () => {
      try {
        const data = await api<T>(path);
        if (!cancelled) setState({ data, error: null, loading: false });
      } catch (err) {
        if (cancelled) return;
        const error = err instanceof ApiError ? err : new ApiError(0, String(err), null);
        if (error.status === 401) {
          router.replace("/login");
          return;
        }
        setState((s) => ({ data: s.data, error, loading: false }));
      }
    };
    void load();
    const id = refreshMs ? window.setInterval(() => void load(), refreshMs) : undefined;
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [path, refreshMs, router, version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { ...state, reload };
}
