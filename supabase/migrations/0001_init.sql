-- Milestone 6: restaurant, tables, customers, reservations, call logs.
--
-- Times: reservations store absolute instants (timestamptz, UTC on disk). The
-- restaurant's IANA time zone converts local "Saturday 19:00" to an instant in
-- the backend. Opening hours are local wall-clock times.
--
-- Double-booking is impossible at the database level: an exclusion constraint
-- forbids two *confirmed* reservations on the same table whose time ranges
-- overlap. The backend checks availability first, but the constraint is what
-- holds under concurrency.

create extension if not exists btree_gist;   -- "=" on uuid inside a GiST exclusion constraint
create extension if not exists pgcrypto;     -- gen_random_uuid() on older Postgres

-- updated_at maintenance ------------------------------------------------------------
create or replace function set_updated_at() returns trigger
language plpgsql as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

-- Restaurants and their booking rules -----------------------------------------------
create table restaurants (
    id                             uuid primary key default gen_random_uuid(),
    slug                           text not null unique check (slug ~ '^[a-z0-9-]{2,60}$'),
    name                           text not null check (length(name) between 1 and 120),
    cuisine                        text not null default '',
    description                    text not null default '',
    address                        text not null default '',
    phone                          text not null default '',
    email                          text not null default '',
    timezone                       text not null,        -- IANA name, e.g. 'Asia/Karachi'
    booking_reference_prefix       text not null default 'RS' check (booking_reference_prefix ~ '^[A-Z]{2,4}$'),
    -- Reservation rules
    min_party_size                 int  not null default 1  check (min_party_size >= 1),
    max_party_size                 int  not null default 10 check (max_party_size >= min_party_size),
    booking_window_days            int  not null default 30 check (booking_window_days between 1 and 365),
    last_seating_minutes_before_close int not null default 60 check (last_seating_minutes_before_close >= 0),
    min_notice_minutes             int  not null default 30 check (min_notice_minutes >= 0),
    dining_duration_minutes        int  not null default 90 check (dining_duration_minutes between 15 and 480),
    slot_interval_minutes          int  not null default 15 check (slot_interval_minutes between 5 and 60),
    require_phone                  boolean not null default false,
    services                       text[] not null default '{}',
    policies                       text[] not null default '{}',
    created_at                     timestamptz not null default now(),
    updated_at                     timestamptz not null default now()
);
create trigger restaurants_updated_at before update on restaurants
    for each row execute function set_updated_at();

-- One row per open weekday (0 = Monday ... 6 = Sunday). No row = closed that day.
create table restaurant_hours (
    id             uuid primary key default gen_random_uuid(),
    restaurant_id  uuid not null references restaurants(id) on delete cascade,
    weekday        smallint not null check (weekday between 0 and 6),
    opens_at       time not null,
    closes_at      time not null,
    check (closes_at > opens_at),            -- hours past midnight are not supported yet
    unique (restaurant_id, weekday)
);

create table restaurant_tables (
    id             uuid primary key default gen_random_uuid(),
    restaurant_id  uuid not null references restaurants(id) on delete cascade,
    label          text not null check (length(label) between 1 and 20),
    seats          int  not null check (seats between 1 and 30),
    min_party_size int  not null default 1 check (min_party_size >= 1),
    area           text not null default 'main',
    is_active      boolean not null default true,
    created_at     timestamptz not null default now(),
    unique (restaurant_id, label),
    check (min_party_size <= seats)
);
create index restaurant_tables_lookup on restaurant_tables (restaurant_id, is_active, seats);

-- Customers --------------------------------------------------------------------------
create table customers (
    id             uuid primary key default gen_random_uuid(),
    restaurant_id  uuid not null references restaurants(id) on delete cascade,
    name           text not null check (length(name) between 1 and 80),
    phone          text check (phone ~ '^\+?[0-9]{7,15}$'),
    email          text,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);
create trigger customers_updated_at before update on customers
    for each row execute function set_updated_at();
-- A phone number identifies a returning customer.
create unique index customers_phone_unique on customers (restaurant_id, phone) where phone is not null;
create index customers_name on customers (restaurant_id, lower(name));

-- Reservations -----------------------------------------------------------------------
create table reservations (
    id                 uuid primary key default gen_random_uuid(),
    restaurant_id      uuid not null references restaurants(id) on delete cascade,
    table_id           uuid not null references restaurant_tables(id),
    customer_id        uuid not null references customers(id),
    booking_reference  text not null unique check (booking_reference ~ '^[A-Z]{2,4}-[A-Z0-9]{6}$'),
    party_size         int  not null check (party_size between 1 and 30),
    starts_at          timestamptz not null,
    ends_at            timestamptz not null,
    status             text not null default 'confirmed'
                       check (status in ('confirmed', 'cancelled', 'completed', 'no_show')),
    special_requests   text check (length(special_requests) <= 300),
    source             text not null default 'voice' check (source in ('voice', 'web', 'admin')),
    voice_session_id   text,
    cancelled_at       timestamptz,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now(),
    check (ends_at > starts_at),
    check ((status = 'cancelled') = (cancelled_at is not null)),
    -- The double-booking guard: one confirmed reservation per table per moment.
    constraint reservations_no_overlap exclude using gist (
        table_id with =,
        tstzrange(starts_at, ends_at, '[)') with &&
    ) where (status = 'confirmed')
);
create trigger reservations_updated_at before update on reservations
    for each row execute function set_updated_at();
create index reservations_by_time on reservations (restaurant_id, starts_at) where status = 'confirmed';
create index reservations_by_customer on reservations (customer_id);
create index reservations_by_session on reservations (voice_session_id) where voice_session_id is not null;

-- Voice sessions (call logs) ---------------------------------------------------------
create table voice_sessions (
    id                  text primary key,        -- the WebRTC peer connection id
    restaurant_id       uuid references restaurants(id) on delete set null,
    status              text not null check (status in ('connecting', 'active', 'ended', 'error')),
    end_reason          text check (end_reason in ('caller_hung_up', 'assistant_ended', 'caller_silent',
                                                   'connect_timeout', 'error', 'server_shutdown')),
    started_at          timestamptz not null,
    connected_at        timestamptz,
    ready_at            timestamptz,
    ended_at            timestamptz,
    error               text,
    errors              text[] not null default '{}',
    -- Latency and audio-health numbers (see VoiceSession): stt/response/voice latencies,
    -- dropped audio, loop stalls, tool calls.
    metrics             jsonb not null default '{}'::jsonb,
    created_at          timestamptz not null default now()
);
create index voice_sessions_recent on voice_sessions (started_at desc);

create table conversation_messages (
    id           bigint generated always as identity primary key,
    session_id   text not null references voice_sessions(id) on delete cascade,
    position     int  not null,
    role         text not null check (role in ('user', 'assistant')),
    content      text not null,
    interrupted  boolean not null default false,
    spoken_at    timestamptz not null,
    unique (session_id, position)
);

-- Assistant personality --------------------------------------------------------------
create table assistant_settings (
    restaurant_id     uuid primary key references restaurants(id) on delete cascade,
    assistant_name    text not null default 'Sara' check (length(assistant_name) between 1 and 40),
    greeting          text,                       -- null = built from the restaurant name
    personality       text not null default 'warm, professional and concise',
    voice_id          text,                       -- null = ELEVENLABS_VOICE_ID / default voice
    updated_at        timestamptz not null default now()
);
create trigger assistant_settings_updated_at before update on assistant_settings
    for each row execute function set_updated_at();

-- Supabase exposes the public schema over its REST API. Only the backend (with the
-- database password) uses these tables, so keep them closed to the anon/authenticated
-- roles: enable row level security with no policies.
alter table restaurants           enable row level security;
alter table restaurant_hours      enable row level security;
alter table restaurant_tables     enable row level security;
alter table customers             enable row level security;
alter table reservations          enable row level security;
alter table voice_sessions        enable row level security;
alter table conversation_messages enable row level security;
alter table assistant_settings    enable row level security;
