import type { Metadata } from "next";

import { DashboardView } from "@/components/dashboard/dashboard-view";
import { AdminGate } from "@/components/shell/admin-gate";

export const metadata: Metadata = {
  title: "Dashboard · Restaurant Voice Assistant",
};

export default function DashboardPage() {
  return (
    <AdminGate>
      <DashboardView />
    </AdminGate>
  );
}
