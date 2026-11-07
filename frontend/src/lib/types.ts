/** Shapes returned by the management API (backend app/api/*). */

export type ReservationStatus = "confirmed" | "cancelled" | "completed" | "no_show";

export type Reservation = {
  id: string;
  booking_reference: string;
  customer_name: string;
  phone: string | null;
  guests: number;
  date: string;
  weekday: string;
  time: string;
  table: string;
  area: string;
  status: ReservationStatus;
  source: "voice" | "web" | "admin";
  special_requests: string | null;
  voice_session_id: string | null;
  created_at: string;
  cancelled_at: string | null;
};

export type Dashboard = {
  restaurant_name: string;
  today: string;
  stats: {
    total_reservations: number;
    cancelled_reservations: number;
    today_reservations: number;
    today_guests: number;
    upcoming_reservations: number;
    total_calls: number;
    successful_bookings: number;
    booking_rate: number | null;
    avg_call_seconds: number | null;
  };
  bookings_next_7_days: { date: string; weekday: string; bookings: number; guests: number }[];
  calls_last_7_days: { date: string; weekday: string; calls: number }[];
  recent_calls: {
    id: string;
    started_at: string;
    status: string;
    end_reason: string | null;
    error: string | null;
    duration_seconds: number | null;
    first_request: string | null;
    booking_reference: string | null;
  }[];
  recent_activity: Reservation[];
};

export type CallSummary = {
  id: string;
  status: "connecting" | "active" | "ended" | "error";
  end_reason: string | null;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  message_count: number;
  first_request: string | null;
  booking_reference: string | null;
  error: string | null;
  error_count: number;
  live: boolean;
};

export type CallDetail = {
  id: string;
  status: string;
  end_reason: string | null;
  started_at: string;
  connected_at: string | null;
  ready_at: string | null;
  ended_at: string | null;
  error: string | null;
  errors: string[];
  metrics: Record<string, unknown>;
  transcript: { role: "user" | "assistant"; content: string; interrupted: boolean; spoken_at: string }[];
  reservations: Reservation[];
  live: boolean;
};

export type DayHours = { opens: string; closes: string } | null;

export type TableRow = {
  id: string;
  label: string;
  seats: number;
  min_party_size: number;
  area: string;
  is_active: boolean;
};

export type RestaurantSettings = {
  id: string;
  slug: string;
  name: string;
  cuisine: string;
  description: string;
  address: string;
  phone: string;
  email: string;
  timezone: string;
  min_party_size: number;
  max_party_size: number;
  booking_window_days: number;
  last_seating_minutes_before_close: number;
  min_notice_minutes: number;
  dining_duration_minutes: number;
  slot_interval_minutes: number;
  require_phone: boolean;
  services: string[];
  policies: string[];
};

export type AssistantSettings = {
  assistant_name: string;
  greeting: string | null;
  personality: string;
  voice_id: string | null;
};

export type SettingsBundle = {
  restaurant: RestaurantSettings;
  hours: DayHours[];
  tables: TableRow[];
  assistant: AssistantSettings;
};

export type BookingProblem = {
  detail: string;
  code: string;
  alternatives: { date: string; time: string }[];
};
