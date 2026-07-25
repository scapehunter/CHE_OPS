"""
Stage 1 itinerary logic: builds a Day x (Morning/Afternoon/Evening) grid for the whole
plan duration, with arrival/departure, assembly/check-in buffers, and road-transfer
entries (including washroom-break and meal-overlap notes) auto-placed based on the
rules loaded from rules.json. Kept free of any Streamlit imports so this can be tested
directly.
"""
import json
from datetime import datetime, timedelta


def load_rules(path="rules.json"):
    with open(path) as f:
        return json.load(f)


def get_program(program_name, programs):
    """Returns the program dict (name + airports list) matching program_name, or None."""
    for p in programs:
        if p["name"] == program_name:
            return p
    return None


def _time_to_minutes(t):
    return t.hour * 60 + t.minute


def _parse_hhmm(s):
    h, m = map(int, s.split(":"))
    return h * 60 + m


def classify_time_slot(t, time_slots):
    """Returns the slot name (e.g. 'Morning') that time t falls into, per the
    time_slots definition from rules.json. 'end': '24:00' is treated as end-of-day."""
    minutes = _time_to_minutes(t)
    for slot in time_slots:
        start_minutes = _parse_hhmm(slot["start"])
        end_minutes = 24 * 60 if slot["end"] == "24:00" else _parse_hhmm(slot["end"])
        if start_minutes <= minutes < end_minutes:
            return slot["name"]
    return time_slots[-1]["name"] if time_slots else "Unknown"


def _meal_overlaps(window_start_dt, window_end_dt, meal_rules):
    """
    Checks [window_start_dt, window_end_dt) against every meal window (from meal_rules)
    on both the start and end dates involved - covers the case where the interval spans
    midnight without assuming meals happen at the same clock time every day (they do,
    per the rules, but this keeps the check honest about which calendar date it's
    checking rather than silently assuming just one).
    Returns a list of meal names that overlap, in window order, deduplicated.
    """
    overlaps = []
    for d in {window_start_dt.date(), window_end_dt.date()}:
        for meal in meal_rules:
            m_start = datetime.combine(d, datetime.min.time()) + timedelta(minutes=_parse_hhmm(meal["window_start"]))
            m_end = datetime.combine(d, datetime.min.time()) + timedelta(minutes=_parse_hhmm(meal["window_end"]))
            if window_start_dt < m_end and window_end_dt > m_start:
                overlaps.append(meal["name"])
    seen = set()
    result = []
    for name in overlaps:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def build_stage1_grid(
    plan_name, start_date, end_date,
    arrival_airport, arrival_time, arrival_travel_minutes,
    departure_airport, departure_time, departure_travel_minutes,
    program_location, time_slots, buffer_rules, transfer_rules, meal_rules,
):
    """
    Returns a dict:
      {
        "plan_name": ..., "program_location": ...,
        "days": [
          {"label": "Day 1 (17 Feb 2025)", "date": date, "Morning": "...", "Afternoon": "...", "Evening": "..."},
          ...
        ]
      }

    Builds, using real datetime arithmetic so long transfers/buffers can correctly roll
    into an adjacent calendar day (e.g. a late arrival plus a long transfer landing after
    midnight):
      - Arrival entry, on start_date at arrival_time.
      - Road transfer from arrival_airport to program_location: departs per the
        "depart_for_hotel" buffer_rule (offset from flight_arrival), takes
        arrival_travel_minutes, and gets a washroom-break note if that exceeds
        transfer_rules' threshold, plus a meal-overlap note if the transfer window
        overlaps any meal_rules window.
      - Assembly and Check-in entries (from buffer_rules, offset from flight_departure).
      - Road transfer from program_location to departure_airport: arrives in time for
        the *earliest* of the assembly/check-in buffer entries, so a long transfer
        doesn't leave too little prep time - same washroom-break/meal-overlap notes.
      - Departure entry, on end_date at departure_time.

    Any computed time that falls outside the plan's date range (e.g. a very short trip
    combined with a long transfer) is clamped to the nearest existing day, with a note
    flagging that it spilled outside the plan dates, rather than silently dropped.

    Also returns "locked_slots": a set of (date, slot_name) pairs that received
    auto-generated logistics content (arrival, transfers, assembly, check-in, departure).
    These are meant to never be edited or added to via any Stage 2 activity UI - use
    compute_addable_slots() to find which cells are actually open for activities.
    """
    num_days = (end_date - start_date).days + 1
    days = []
    for i in range(num_days):
        day_date = start_date + timedelta(days=i)
        days.append({
            "label": f"Day {i + 1} ({day_date.strftime('%d %b %Y')})",
            "date": day_date,
            "Morning": "", "Afternoon": "", "Evening": "",
        })
    day_dates = [d["date"] for d in days]
    locked_slots = set()

    def add_entry(dt, text):
        target_date = dt.date()
        spilled_note = ""
        if target_date not in day_dates:
            target_date = day_dates[0] if target_date < day_dates[0] else day_dates[-1]
            spilled_note = " ⚠️ falls outside plan dates - review timing"
        for day in days:
            if day["date"] == target_date:
                slot = classify_time_slot(dt.time(), time_slots)
                existing = day[slot]
                new_text = text + spilled_note
                day[slot] = f"{existing}\n{new_text}" if existing else new_text
                locked_slots.add((target_date, slot))
                return

    def transfer_notes(minutes, window_start_dt, window_end_dt):
        notes = []
        threshold = transfer_rules.get("washroom_break_threshold_minutes")
        if threshold and minutes > threshold:
            notes.append("washroom break recommended")
        overlapping_meals = _meal_overlaps(window_start_dt, window_end_dt, meal_rules)
        if overlapping_meals:
            notes.append(f"overlaps {', '.join(overlapping_meals)} - arrange meal stop or packed meal")
        return f" ({'; '.join(notes)})" if notes else ""

    arrival_dt = datetime.combine(start_date, arrival_time)
    departure_dt = datetime.combine(end_date, departure_time)

    # --- Arrival day ---
    add_entry(arrival_dt, f"Arrival: {arrival_airport}, {arrival_time.strftime('%H:%M')}")

    depart_for_program_rule = next((r for r in buffer_rules if r["id"] == "depart_for_hotel"), None)
    if depart_for_program_rule:
        depart_for_program_dt = arrival_dt + timedelta(minutes=depart_for_program_rule["offset_minutes"])
        program_arrival_dt = depart_for_program_dt + timedelta(minutes=arrival_travel_minutes)
        note = transfer_notes(arrival_travel_minutes, depart_for_program_dt, program_arrival_dt)
        add_entry(depart_for_program_dt, f"{depart_for_program_rule['label']}{note}")
        add_entry(program_arrival_dt, f"Arrival at {program_location or 'program location'}")

    # --- Departure day ---
    departure_side_rules = [r for r in buffer_rules if r["anchor"] == "flight_departure"]
    departure_side_times = {}
    for rule in departure_side_rules:
        t = departure_dt + timedelta(minutes=rule["offset_minutes"])
        departure_side_times[rule["id"]] = t
        add_entry(t, rule["label"])

    if departure_side_times:
        earliest_prep_dt = min(departure_side_times.values())
        depart_program_for_airport_dt = earliest_prep_dt - timedelta(minutes=departure_travel_minutes)
        note = transfer_notes(departure_travel_minutes, depart_program_for_airport_dt, earliest_prep_dt)
        add_entry(depart_program_for_airport_dt, f"Depart {program_location or 'program location'} for airport{note}")

    add_entry(departure_dt, f"Departure: {departure_airport}, {departure_time.strftime('%H:%M')}")

    return {
        "plan_name": plan_name, "program_location": program_location,
        "days": days, "locked_slots": locked_slots,
    }


def compute_addable_slots(days, locked_slots, start_date, end_date, arrival_time, departure_time, time_slots):
    """
    Returns the set of (date, slot_name) pairs open for Stage 2 activity addition:
    - never a slot in locked_slots (auto-generated logistics content - permanently
      off-limits, per "don't allow removal of already populated data")
    - on start_date, only slots strictly after the one containing arrival_time
    - on end_date, only slots strictly before the one containing departure_time
    - on any day strictly between start_date and end_date, all slots are eligible

    Slots that already have a person-added activity in them (not auto-generated) are
    still included here - "no removal" doesn't mean "no more than one activity per
    slot", it just means existing content is never overwritten. The caller appends
    rather than replaces when writing to an addable slot.
    """
    slot_order = [s["name"] for s in time_slots]
    arrival_slot_idx = slot_order.index(classify_time_slot(arrival_time, time_slots))
    departure_slot_idx = slot_order.index(classify_time_slot(departure_time, time_slots))

    addable = set()
    for day in days:
        if day["date"] < start_date or day["date"] > end_date:
            continue
        for slot_idx, slot_name in enumerate(slot_order):
            if (day["date"], slot_name) in locked_slots:
                continue
            if day["date"] == start_date and slot_idx <= arrival_slot_idx:
                continue
            if day["date"] == end_date and slot_idx >= departure_slot_idx:
                continue
            addable.add((day["date"], slot_name))
    return addable