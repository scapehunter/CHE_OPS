"""
Stage 1 itinerary logic: builds a Day x (Morning/Afternoon/Evening) grid for the whole
plan duration, with arrival/departure, assembly/check-in buffers, and road-transfer
entries (including washroom-break and meal-overlap notes) auto-placed based on the
rules loaded from rules.json. Kept free of any Streamlit imports so this can be tested
directly.
"""
import json
from collections import defaultdict
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

    Also returns "timed_events": the same auto-generated entries as a flat list of
    {"datetime": ..., "label": ..., "type": ...} dicts with their exact computed time
    preserved (the day/slot grid only keeps bucketed display text, which loses the
    precise time) - this is what Stage 3 timeline generation consumes.
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
    timed_events = []

    def add_entry(dt, text, event_type):
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
                # Keep the original dt (not the clamped target_date) for Stage 3 - the
                # ⚠️ note already flags the clamp; Stage 3 sorts by real time, and an
                # out-of-range event sorting to its true (if odd) position is more
                # honest than silently pretending it happened at a valid time.
                timed_events.append({"datetime": dt, "label": text + spilled_note, "type": event_type})
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
    add_entry(arrival_dt, f"Arrival: {arrival_airport}, {arrival_time.strftime('%H:%M')}", "Arrival")

    depart_for_program_rule = next((r for r in buffer_rules if r["id"] == "depart_for_hotel"), None)
    if depart_for_program_rule:
        depart_for_program_dt = arrival_dt + timedelta(minutes=depart_for_program_rule["offset_minutes"])
        program_arrival_dt = depart_for_program_dt + timedelta(minutes=arrival_travel_minutes)
        note = transfer_notes(arrival_travel_minutes, depart_for_program_dt, program_arrival_dt)
        add_entry(depart_for_program_dt, f"{depart_for_program_rule['label']}{note}", "Road Transfer")
        add_entry(program_arrival_dt, f"Arrival at {program_location or 'program location'}", "Road Transfer")

    # --- Departure day ---
    departure_side_rules = [r for r in buffer_rules if r["anchor"] == "flight_departure"]
    departure_side_times = {}
    for rule in departure_side_rules:
        t = departure_dt + timedelta(minutes=rule["offset_minutes"])
        departure_side_times[rule["id"]] = t
        add_entry(t, rule["label"], rule["type"])

    if departure_side_times:
        earliest_prep_dt = min(departure_side_times.values())
        depart_program_for_airport_dt = earliest_prep_dt - timedelta(minutes=departure_travel_minutes)
        note = transfer_notes(departure_travel_minutes, depart_program_for_airport_dt, earliest_prep_dt)
        add_entry(depart_program_for_airport_dt, f"Depart {program_location or 'program location'} for airport{note}", "Road Transfer")

    add_entry(departure_dt, f"Departure: {departure_airport}, {departure_time.strftime('%H:%M')}", "Departure")

    return {
        "plan_name": plan_name, "program_location": program_location,
        "days": days, "locked_slots": locked_slots, "timed_events": timed_events,
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


def build_stage3_timeline(timed_events, stage2_activities, meal_rules, default_slot_starts, accommodation_details=None):
    """
    Merges Stage 1's exact-timed logistics events with Stage 2's slot-level activities
    into one chronologically sorted timewise itinerary.

    stage2_activities: list of dicts, one per activity/meal added in Stage 2:
      {"date": date, "slot": slot_name, "order": int (insertion order, for sequencing
       multiple items in the same slot), "kind": "Activity" or "Meal", "name": str,
       "duration_minutes": int, "transfer_required": bool, "transfer_minutes": int or None}

    default_slot_starts: dict of slot_name -> "HH:MM", the assumed start-of-day-part time
      used to anchor a slot's activities when nothing else establishes a start time
      (a locked slot already has exact times from Stage 1; only Stage 2 slots need this).
      This is an assumption, not something the person stated explicitly - it's stored in
      rules.json so it can be corrected without a code change.

    accommodation_details: string used to label the "transfer back" rows for items that
      require a transfer - e.g. "Transfer back to Nirvana Shillong" - falls back to a
      generic "accommodation" if not given.

    For each (date, slot) group of Stage 2 items, sequenced in insertion order starting
    from default_slot_starts[slot]. An item WITHOUT a transfer is a single row, as before.
    An item WITH a transfer expands into five rows - e.g. for an 11:00 start, 30 min
    transfer, 30 min duration:
      Transfer to X       - 11:00
      Arrival at X        - 11:30
      X finished          - 12:00
      Transfer back to Y  - 12:00
      Arrival at Y        - 12:30
    A "Meal" kind is snapped forward to its rule's window_start if the moment it would
    actually start (i.e. after any transfer, not when the transfer begins) is earlier
    than that, and flagged with a note if it would start after the window closes -
    the whole chain (including the preceding transfer-to time) shifts together so the
    transfer duration itself stays correct.

    Returns a list of {"Date": str, "Time": str, "Activity": str, "Type": str, "Notes":
    str} rows, sorted chronologically across the whole plan (locked Stage 1 slots and
    open Stage 2 slots never overlap in practice, by construction of compute_addable_slots,
    so there's no ordering ambiguity between the two sources within a single slot).
    """
    accommodation_label = accommodation_details or "accommodation"
    rows = []
    for event in timed_events:
        rows.append({"dt": event["datetime"], "Activity": event["label"], "Type": event["type"], "Notes": ""})

    grouped = defaultdict(list)
    for a in stage2_activities:
        grouped[(a["date"], a["slot"])].append(a)

    meal_rule_by_name = {m["name"]: m for m in meal_rules}

    for (day_date, slot_name), activities in grouped.items():
        activities_sorted = sorted(activities, key=lambda a: a["order"])
        start_str = default_slot_starts.get(slot_name, "09:00")
        start_minutes = _parse_hhmm(start_str)
        running_dt = datetime.combine(day_date, datetime.min.time()) + timedelta(minutes=start_minutes)

        for a in activities_sorted:
            has_transfer = bool(a["transfer_required"] and a["transfer_minutes"])
            transfer_to_dt = running_dt
            start_dt = running_dt + timedelta(minutes=a["transfer_minutes"]) if has_transfer else running_dt

            note = ""
            if a["kind"] == "Meal":
                meal_rule = meal_rule_by_name.get(a["name"])
                if meal_rule:
                    window_start_dt = datetime.combine(day_date, datetime.min.time()) + timedelta(
                        minutes=_parse_hhmm(meal_rule["window_start"]))
                    window_end_dt = datetime.combine(day_date, datetime.min.time()) + timedelta(
                        minutes=_parse_hhmm(meal_rule["window_end"]))
                    if start_dt < window_start_dt:
                        # Shift the whole chain (including the transfer-to time) forward
                        # together, so the transfer duration itself stays correct.
                        shift = window_start_dt - start_dt
                        start_dt = window_start_dt
                        transfer_to_dt = transfer_to_dt + shift
                    if start_dt > window_end_dt:
                        note = f"⚠️ outside usual {a['name']} window ({meal_rule['window_start']}-{meal_rule['window_end']})"

            if has_transfer:
                rows.append({"dt": transfer_to_dt, "Activity": f"Transfer to {a['name']}", "Type": "Road Transfer", "Notes": ""})
                rows.append({"dt": start_dt, "Activity": f"Arrival at {a['name']}", "Type": "Road Transfer", "Notes": note})
                finished_dt = start_dt + timedelta(minutes=a["duration_minutes"])
                rows.append({"dt": finished_dt, "Activity": f"{a['name']} finished", "Type": a["kind"], "Notes": ""})
                transfer_back_dt = finished_dt
                rows.append({"dt": transfer_back_dt, "Activity": f"Transfer back to {accommodation_label}", "Type": "Road Transfer", "Notes": ""})
                arrival_back_dt = transfer_back_dt + timedelta(minutes=a["transfer_minutes"])
                rows.append({"dt": arrival_back_dt, "Activity": f"Arrival at {accommodation_label}", "Type": "Road Transfer", "Notes": ""})
                running_dt = arrival_back_dt
            else:
                rows.append({"dt": start_dt, "Activity": a["name"], "Type": a["kind"], "Notes": note})
                running_dt = start_dt + timedelta(minutes=a["duration_minutes"])

    rows.sort(key=lambda r: r["dt"])
    return [
        {
            "Date": r["dt"].strftime("%d %b %Y"),
            "Time": r["dt"].strftime("%H:%M"),
            "Activity": r["Activity"],
            "Type": r["Type"],
            "Notes": r["Notes"],
        }
        for r in rows
    ]