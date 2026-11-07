import type { Metadata } from "next";

import { CallsView } from "@/components/calls/calls-view";
import { AdminGate } from "@/components/shell/admin-gate";

export const metadata: Metadata = {
  title: "Call Logs · Restaurant Voice Assistant",
};

export default function CallsPage() {
  return (
    <AdminGate>
      <CallsView />
    </AdminGate>
  );
}
