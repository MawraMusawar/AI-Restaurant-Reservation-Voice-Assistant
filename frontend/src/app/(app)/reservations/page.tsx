import type { Metadata } from "next";

import { ReservationsView } from "@/components/reservations/reservations-view";
import { AdminGate } from "@/components/shell/admin-gate";

export const metadata: Metadata = {
  title: "Reservations · Restaurant Voice Assistant",
};

export default function ReservationsPage() {
  return (
    <AdminGate>
      <ReservationsView />
    </AdminGate>
  );
}
