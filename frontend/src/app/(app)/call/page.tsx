import type { Metadata } from "next";

import { CallScreen } from "@/components/call/call-screen";
import { PageHeader } from "@/components/ui";

export const metadata: Metadata = {
  title: "Live Call · Restaurant Voice Assistant",
};

export default function CallPage() {
  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-6">
      <PageHeader
        title="Live call"
        description="Talk to the receptionist as a caller would: book a table, ask about the restaurant, interrupt any time. Say goodbye and it hangs up."
      />
      <CallScreen />
    </div>
  );
}
