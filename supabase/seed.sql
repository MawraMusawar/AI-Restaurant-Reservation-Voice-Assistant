-- Demo restaurant "Olive & Ember" (fictional). Safe to re-run: existing rows are updated
-- or kept. Sample reservations for the client demo are added in Milestone 9.

insert into restaurants (
    slug, name, cuisine, description, address, phone, email, timezone, booking_reference_prefix,
    min_party_size, max_party_size, booking_window_days, last_seating_minutes_before_close,
    min_notice_minutes, dining_duration_minutes, slot_interval_minutes, require_phone,
    services, policies
) values (
    'olive-and-ember',
    'Olive & Ember',
    'Mediterranean grill',
    'A relaxed Mediterranean grill with wood-fired dishes and a garden terrace.',
    '14 Garden Avenue, Lahore',
    '+92 42 0000 0000',
    'reservations@olive-and-ember.example',
    'Asia/Karachi',
    'OE',
    1, 10, 30, 60, 30, 90, 15, false,
    array[
        'Dine-in with indoor and garden terrace seating',
        'Takeaway',
        'Private dining room for up to 20 guests (arranged by email)',
        'Vegetarian, vegan and gluten-free options',
        'Free parking'
    ],
    array[
        'Tables are held for 15 minutes after the booking time.',
        'Parties larger than 10 are arranged by email.',
        'Children are welcome; high chairs are available.'
    ]
)
on conflict (slug) do update set
    name = excluded.name,
    cuisine = excluded.cuisine,
    description = excluded.description,
    address = excluded.address,
    phone = excluded.phone,
    email = excluded.email,
    timezone = excluded.timezone,
    services = excluded.services,
    policies = excluded.policies;

-- Closed on Mondays (weekday 0 has no row).
insert into restaurant_hours (restaurant_id, weekday, opens_at, closes_at)
select r.id, h.weekday, h.opens_at, h.closes_at
from restaurants r
cross join (values
    (1, time '12:00', time '22:30'),   -- Tuesday
    (2, time '12:00', time '22:30'),   -- Wednesday
    (3, time '12:00', time '22:30'),   -- Thursday
    (4, time '12:00', time '23:30'),   -- Friday
    (5, time '12:00', time '23:30'),   -- Saturday
    (6, time '12:00', time '21:30')    -- Sunday
) as h(weekday, opens_at, closes_at)
where r.slug = 'olive-and-ember'
on conflict (restaurant_id, weekday) do nothing;

-- 12 tables, 54 seats.
insert into restaurant_tables (restaurant_id, label, seats, min_party_size, area)
select r.id, t.label, t.seats, t.min_party_size, t.area
from restaurants r
cross join (values
    ('T1',  2, 1, 'main'),
    ('T2',  2, 1, 'main'),
    ('T3',  2, 1, 'window'),
    ('T4',  2, 1, 'window'),
    ('T5',  4, 1, 'main'),
    ('T6',  4, 1, 'main'),
    ('T7',  4, 2, 'terrace'),
    ('T8',  4, 2, 'terrace'),
    ('T9',  6, 3, 'main'),
    ('T10', 6, 3, 'terrace'),
    ('T11', 8, 5, 'main'),
    ('T12', 10, 6, 'main')
) as t(label, seats, min_party_size, area)
where r.slug = 'olive-and-ember'
on conflict (restaurant_id, label) do nothing;

insert into assistant_settings (restaurant_id, assistant_name, personality)
select id, 'Sara', 'warm, professional and concise'
from restaurants where slug = 'olive-and-ember'
on conflict (restaurant_id) do nothing;
