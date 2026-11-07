import type { Metadata } from "next";

import { LoginForm } from "@/components/shell/login-form";

export const metadata: Metadata = {
  title: "Sign in · Restaurant Voice Assistant",
};

export default function LoginPage() {
  return (
    <main className="flex flex-1 items-center justify-center px-4 py-16">
      <LoginForm />
    </main>
  );
}
