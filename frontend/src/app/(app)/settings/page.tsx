import type { Metadata } from "next";

import { SettingsView } from "@/components/settings/settings-view";
import { AdminGate } from "@/components/shell/admin-gate";

export const metadata: Metadata = {
  title: "Settings · Restaurant Voice Assistant",
};

export default function SettingsPage() {
  return (
    <AdminGate>
      <SettingsView />
    </AdminGate>
  );
}
