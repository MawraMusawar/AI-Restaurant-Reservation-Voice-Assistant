"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";

import type { AuthStatus } from "@/components/shell/admin-gate";
import { api } from "@/lib/api";

const NAV = [
  { href: "/", label: "Dashboard", icon: "▦" },
  { href: "/call", label: "Live call", icon: "☎" },
  { href: "/reservations", label: "Reservations", icon: "▤" },
  { href: "/calls", label: "Call logs", icon: "≋" },
  { href: "/settings", label: "Settings", icon: "⚙" },
];

/** Exact match or a sub-path ("/calls" must not light up "/call"). */
function isActive(pathname: string, href: string): boolean {
  return pathname === href || (href !== "/" && pathname.startsWith(`${href}/`));
}

/** Sidebar navigation (top bar on small screens) around every page except /login. */
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [signedIn, setSignedIn] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    api<AuthStatus>("/api/auth/me")
      .then((me) => !cancelled && setSignedIn(me.signed_in))
      .catch(() => !cancelled && setSignedIn(false));
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  const signOut = async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    setSignedIn(false);
    router.push("/login");
  };

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside className="border-b border-zinc-200 bg-white md:sticky md:top-0 md:flex md:h-screen md:w-60 md:shrink-0 md:flex-col md:border-r md:border-b-0 dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex items-center justify-between gap-3 px-5 py-4">
          <Link href="/" className="flex items-center gap-2 font-semibold">
            <span aria-hidden className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-600 text-white">
              🍽
            </span>
            <span className="leading-tight">
              Voice Receptionist
              <span className="block text-xs font-normal text-zinc-500 dark:text-zinc-400">AI restaurant reservations</span>
            </span>
          </Link>
        </div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-1 md:flex-col md:overflow-visible">
          {NAV.map((item) => {
            const active = isActive(pathname, item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`flex shrink-0 items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition ${
                  active
                    ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                    : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                }`}
              >
                <span aria-hidden className="w-4 text-center">
                  {item.icon}
                </span>
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="hidden border-t border-zinc-200 px-5 py-4 text-sm md:block dark:border-zinc-800">
          {signedIn ? (
            <button type="button" onClick={signOut} className="text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100">
              Sign out
            </button>
          ) : signedIn === false ? (
            <Link href="/login" className="text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100">
              Staff sign in
            </Link>
          ) : null}
        </div>
      </aside>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-8">{children}</main>
    </div>
  );
}
