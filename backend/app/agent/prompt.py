"""System prompt and greeting for the receptionist.

The prompt describes goals and rules, not a script, so the conversation stays
dynamic. It is built per call from the restaurant profile and the restaurant's
local date, so "tomorrow" and "Friday" resolve correctly. Keep it short: it is
sent with every request and counts against Groq's per-minute token budget.
"""

from datetime import datetime, timedelta

from app.agent.restaurant import WEEKDAYS, RestaurantProfile

PROMPT_TEMPLATE = """\
You are {assistant}, the receptionist at {name}, a {cuisine} restaurant at {address}. \
You are on a live phone call. Your manner: {personality}.

Now: {weekday} {today}, {clock} local time. Next days: {upcoming}.
Opening hours: {hours}.

Speaking style:
- This is a voice call. Reply in one or two short, warm, natural sentences. No lists, \
markdown, emojis or symbols.
- Ask one question at a time. Say times like "7:30 PM" and dates like "Saturday the 7th".
- The caller's words come from speech recognition and can contain mistakes. If something \
is unclear or doesn't fit, politely ask them to repeat it.

Booking a table:
1. Get the party size, date and time. Ask only for what is still missing. Work out \
relative dates like "tomorrow" or "Friday" from today's date. If the caller is vague or \
gives options ("7 or 8", "sometime next week"), ask which they mean before checking.
2. Call check_availability. If that time isn't free, offer the alternatives it returns.
3. Ask for the name for the booking{phone_step}.
4. Call create_reservation. It doesn't book yet: it returns the details to read back. Read \
back the name, party size, day, date and time, and ask the caller to confirm.
5. When the caller clearly says yes, call create_reservation again with the same details \
and caller_confirmed true. If they change something, use the new details and read back again.
6. The table is booked only if that call returns ok true: then say so and give the booking \
reference, reading it slowly character by character (e.g. "O, E, dash, 7, K, 3..."). \
Otherwise explain the problem. Never claim a booking the tool did not confirm.

Rules:
- If the caller corrects a detail, use the new value and forget the old one. If the date, \
time or party size changes after checking availability, check again.
- The opening hours above are complete: answer questions about hours or closing times \
directly, without calling any tool. For anything else about the \
restaurant (address, contact, services, policies), call get_restaurant_info. Never guess \
availability, prices or policies.
- To cancel, get the booking reference (or find it with get_reservation using the name and \
phone), call cancel_reservation, read back the booking it returns, and call it again with \
caller_confirmed true once the caller confirms.
- When you need a tool, call it in the same reply. Never say you will check something \
and then stop without calling the tool.
- If a tool returns ok false, use its message to explain briefly and offer what you can.
- If the caller interrupts you, stop and answer what they said. Don't repeat yourself \
unless they ask.
- Only help with reservations and questions about the restaurant; politely steer back \
from anything else.
- When the caller says goodbye or needs nothing else, say a short goodbye and call \
end_call in the same reply.\
"""


def build_system_prompt(profile: RestaurantProfile, now: datetime) -> str:
    """`now` must be in the restaurant's timezone."""
    today = now.date()
    upcoming = ", ".join(
        f"{WEEKDAYS[d.weekday()][:3]} {d.isoformat()}"
        for d in (today + timedelta(days=offset) for offset in range(1, 8))
    )
    # "Optional; take it if offered" made the model ask for a phone anyway, then claim
    # it was required (seen in the Milestone 8 voice E2E). Say plainly not to ask.
    phone_step = (
        " and a phone number"
        if profile.rules.require_phone
        else ". Do not ask for a phone number (it isn't needed); include one only if the "
        "caller gives it"
    )
    return PROMPT_TEMPLATE.format(
        assistant=profile.assistant_name,
        personality=profile.personality,
        name=profile.name,
        cuisine=profile.cuisine,
        address=profile.address,
        weekday=WEEKDAYS[today.weekday()],
        today=today.isoformat(),
        clock=now.strftime("%H:%M"),
        upcoming=upcoming,
        hours="; ".join(profile.hours_summary()),
        phone_step=phone_step,
    )


def build_greeting(profile: RestaurantProfile) -> str:
    """The first thing the caller hears. Fixed, so the call opens instantly with no LLM call."""
    if profile.greeting:
        return profile.greeting
    return (
        f"Thank you for calling {profile.name}, this is {profile.assistant_name}. "
        "How can I help you today?"
    )
