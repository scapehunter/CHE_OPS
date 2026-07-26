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


def apply_washroom_break(minutes, transfer_rules):
    """
    Returns (effective_minutes, was_added). If a transfer exceeds the washroom-break
    threshold, this adds a fixed buffer (washroom_break_minutes, default 20) to the
    travel time itself - not just a note - so downstream timing actually reflects the
    stop, and flags that it was added in the note. Shared by build_stage1_grid and any
    other transfer-generating logic (e.g. a Stage 2 relocation transfer) so both apply
    the same threshold/buffer consistently.
    """
    threshold = transfer_rules.get("washroom_break_threshold_minutes")
    extra = transfer_rules.get("washroom_break_minutes", 20)
    if threshold and minutes > threshold:
        return minutes + extra, True
    return minutes, False


def build_transfer_note(washroom_break_added, window_start_dt, window_end_dt, meal_rules, transfer_rules):
    notes = []
    if washroom_break_added:
        extra = transfer_rules.get("washroom_break_minutes", 20)
        notes.append(f"washroom break recommended (+{extra} min added)")
    overlapping_meals = _meal_overlaps(window_start_dt, window_end_dt, meal_rules)
    if overlapping_meals:
        notes.append(f"overlaps {', '.join(overlapping_meals)} - arrange meal stop or packed meal")
    return f" ({'; '.join(notes)})" if notes else ""


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

    arrival_dt = datetime.combine(start_date, arrival_time)
    departure_dt = datetime.combine(end_date, departure_time)

    # --- Arrival day ---
    add_entry(arrival_dt, f"Arrival: {arrival_airport}, {arrival_time.strftime('%H:%M')}", "Arrival")

    depart_for_program_rule = next((r for r in buffer_rules if r["id"] == "depart_for_hotel"), None)
    if depart_for_program_rule:
        depart_for_program_dt = arrival_dt + timedelta(minutes=depart_for_program_rule["offset_minutes"])
        effective_arrival_travel, washroom_added = apply_washroom_break(arrival_travel_minutes, transfer_rules)
        program_arrival_dt = depart_for_program_dt + timedelta(minutes=effective_arrival_travel)
        note = build_transfer_note(washroom_added, depart_for_program_dt, program_arrival_dt, meal_rules, transfer_rules)
        add_entry(depart_for_program_dt, f"{depart_for_program_rule['label']}{note}", "Road Transfer")
        add_entry(program_arrival_dt, f"Arrival at {program_location or 'program location'}", "Road Transfer")

        # Anchored to the moment the group actually reaches the property - e.g. check-in
        # process and safety briefing, both real-world steps that happen once you're
        # physically at the hotel, not tied to the flight itself.
        for rule in buffer_rules:
            if rule["anchor"] == "hotel_arrival":
                t = program_arrival_dt + timedelta(minutes=rule["offset_minutes"])
                add_entry(t, rule["label"], rule["type"])

    # --- Departure day ---
    departure_side_rules = [r for r in buffer_rules if r["anchor"] == "flight_departure"]
    departure_side_times = {}
    for rule in departure_side_rules:
        t = departure_dt + timedelta(minutes=rule["offset_minutes"])
        departure_side_times[rule["id"]] = t
        add_entry(t, rule["label"], rule["type"])

    if departure_side_times:
        earliest_prep_dt = min(departure_side_times.values())
        effective_departure_travel, washroom_added = apply_washroom_break(departure_travel_minutes, transfer_rules)
        depart_program_for_airport_dt = earliest_prep_dt - timedelta(minutes=effective_departure_travel)
        note = build_transfer_note(washroom_added, depart_program_for_airport_dt, earliest_prep_dt, meal_rules, transfer_rules)
        add_entry(depart_program_for_airport_dt, f"Depart {program_location or 'program location'} for airport{note}", "Road Transfer")

        # Anchored to the moment the group leaves the hotel for the airport - NOT to the
        # flight's departure time. Check-out and bag-loading happen at the property,
        # right before getting in the vehicle - anchoring them to the flight time instead
        # would place them hours too late, after the group has already left for the
        # airport (they'd land in the middle of the road transfer or even after it).
        for rule in buffer_rules:
            if rule["anchor"] == "depart_for_airport":
                t = depart_program_for_airport_dt + timedelta(minutes=rule["offset_minutes"])
                add_entry(t, rule["label"], rule["type"])

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


def expand_activity_or_meal(
    anchor_dt, day_date, kind, name, duration_minutes,
    transfer_required, transfer_minutes,
    meal_stop_required, meal_stop_minutes,
    meal_rules, accommodation_details=None,
):
    """
    Shared expansion logic for a single Activity or Meal entry, starting at anchor_dt.
    Used both by build_stage3_timeline (sequencing Stage 2's slot-level items) and by
    Stage 3's own "Insert Row" dialog (a single item at a person-chosen time) - kept as
    one function so both paths always produce identical row shapes for the same inputs.

    meal_stop_required/meal_stop_minutes only apply when kind == "Activity" and
    transfer_required is True: a meal stop taken during the transfer extends the whole
    transfer's duration (both legs), rather than being inserted as a separate stop -
    e.g. a 60 min transfer with a 30 min meal stop becomes a 90 min transfer each way.

    Returns (rows, end_dt): rows is a list of {"dt", "Activity", "Type", "Notes"} dicts;
    end_dt is when the next sequential item (if any) should start.

    A "Meal" kind is snapped forward to the average (midpoint) of its rule's window if the
    moment it would actually start (i.e. after any transfer, not when the transfer begins)
    is earlier than that, and flagged with a note if it would start after the window
    closes - the whole chain (including the preceding transfer-to time) shifts together
    so the transfer duration itself stays correct.
    """
    accommodation_label = accommodation_details or "accommodation"

    effective_transfer_minutes = None
    if transfer_required and transfer_minutes:
        effective_transfer_minutes = transfer_minutes
        if kind == "Activity" and meal_stop_required and meal_stop_minutes:
            effective_transfer_minutes += meal_stop_minutes

    has_transfer = bool(effective_transfer_minutes)
    transfer_to_dt = anchor_dt
    start_dt = anchor_dt + timedelta(minutes=effective_transfer_minutes) if has_transfer else anchor_dt

    note = ""
    if kind == "Meal":
        meal_rule = {m["name"]: m for m in meal_rules}.get(name)
        if meal_rule:
            window_start_dt = datetime.combine(day_date, datetime.min.time()) + timedelta(
                minutes=_parse_hhmm(meal_rule["window_start"]))
            window_end_dt = datetime.combine(day_date, datetime.min.time()) + timedelta(
                minutes=_parse_hhmm(meal_rule["window_end"]))
            average_dt = window_start_dt + (window_end_dt - window_start_dt) / 2
            if start_dt < average_dt:
                # Nothing earlier in the sequence has already pushed this meal past its
                # natural midpoint - anchor it there rather than at window_start, so a
                # meal added with no other context lands at a genuinely typical time for
                # it (e.g. Lunch defaults to 13:00, the middle of 12:00-14:00) rather than
                # the earliest technically-valid moment.
                shift = average_dt - start_dt
                start_dt = average_dt
                transfer_to_dt = transfer_to_dt + shift
            if start_dt > window_end_dt:
                note = f"⚠️ outside usual {name} window ({meal_rule['window_start']}-{meal_rule['window_end']})"

    rows = []
    if has_transfer:
        transfer_note = ""
        if kind == "Activity" and meal_stop_required and meal_stop_minutes:
            transfer_note = f"Includes {meal_stop_minutes} min meal stop"
        rows.append({"dt": transfer_to_dt, "Activity": f"Transfer to {name}", "Type": "Road Transfer", "Notes": transfer_note})
        rows.append({"dt": start_dt, "Activity": f"{name} starts", "Type": kind, "Notes": note})
        finished_dt = start_dt + timedelta(minutes=duration_minutes)
        rows.append({"dt": finished_dt, "Activity": f"{name} ends", "Type": kind, "Notes": ""})
        transfer_back_dt = finished_dt
        rows.append({"dt": transfer_back_dt, "Activity": f"Transfer back to {accommodation_label}", "Type": "Road Transfer", "Notes": transfer_note})
        arrival_back_dt = transfer_back_dt + timedelta(minutes=effective_transfer_minutes)
        rows.append({"dt": arrival_back_dt, "Activity": f"Arrival at {accommodation_label}", "Type": "Road Transfer", "Notes": ""})
        end_dt = arrival_back_dt
    else:
        rows.append({"dt": start_dt, "Activity": f"{name} starts", "Type": kind, "Notes": note})
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        rows.append({"dt": end_dt, "Activity": f"{name} ends", "Type": kind, "Notes": ""})

    return rows, end_dt


def build_stage3_timeline(timed_events, stage2_activities, meal_rules, default_slot_starts, accommodation_details=None):
    """
    Merges Stage 1's exact-timed logistics events with Stage 2's slot-level activities
    into one chronologically sorted timewise itinerary.

    stage2_activities: list of dicts, one per activity/meal added in Stage 2:
      {"date": date, "slot": slot_name, "order": int (insertion order, for sequencing
       multiple items in the same slot), "kind": "Activity" or "Meal", "name": str,
       "duration_minutes": int, "transfer_required": bool, "transfer_minutes": int or None,
       "meal_stop_required": bool, "meal_stop_minutes": int or None}

    default_slot_starts: dict of slot_name -> "HH:MM", the assumed start-of-day-part time
      used to anchor a slot's activities when nothing else establishes a start time
      (a locked slot already has exact times from Stage 1; only Stage 2 slots need this).
      This is an assumption, not something the person stated explicitly - it's stored in
      rules.json so it can be corrected without a code change.

    accommodation_details: string used to label the "transfer back" rows for items that
      require a transfer - e.g. "Transfer back to Nirvana Shillong" - falls back to a
      generic "accommodation" if not given.

    Each Stage 2 item is expanded via expand_activity_or_meal() and sequenced back to
    back within its (date, slot) group, in insertion order.

    Returns a list of {"Date": str, "Time": str, "Activity": str, "Type": str, "Notes":
    str, "_group": int, "_order": int} rows, sorted chronologically across the whole
    plan. _group identifies which rows came from the same source item (e.g. all 5 rows
    of one transfer-based activity share a _group); _order is that item's insertion
    sequence, used by Stage 3's cascade logic to re-sequence multiple disrupted items by
    the order they were added, once a later insert pushes into them (Stage 1's own
    events all get _order 0, since they exist before any Stage 2 addition).
    """
    rows = []
    group_id = 0
    for event in timed_events:
        rows.append({
            "dt": event["datetime"], "Activity": event["label"], "Type": event["type"], "Notes": "",
            "_group": group_id, "_order": 0,
        })
        group_id += 1

    grouped = defaultdict(list)
    for a in stage2_activities:
        grouped[(a["date"], a["slot"])].append(a)

    for (day_date, slot_name), activities in grouped.items():
        activities_sorted = sorted(activities, key=lambda a: a["order"])
        start_str = default_slot_starts.get(slot_name, "09:00")
        running_dt = datetime.combine(day_date, datetime.min.time()) + timedelta(minutes=_parse_hhmm(start_str))

        for a in activities_sorted:
            new_rows, running_dt = expand_activity_or_meal(
                running_dt, day_date, a["kind"], a["name"], a["duration_minutes"],
                a["transfer_required"], a["transfer_minutes"],
                a.get("meal_stop_required", False), a.get("meal_stop_minutes"),
                meal_rules, accommodation_details,
            )
            this_group = group_id
            group_id += 1
            for r in new_rows:
                r["_group"] = this_group
                r["_order"] = a["order"] + 1  # >0, so Stage 2 items always rank after Stage 1's 0s
            rows.extend(new_rows)

    rows.sort(key=lambda r: r["dt"])
    return [
        {
            "Date": r["dt"].strftime("%d %b %Y"),
            "Time": r["dt"].strftime("%H:%M"),
            "Activity": r["Activity"],
            "Type": r["Type"],
            "Notes": r["Notes"],
            "_group": r["_group"],
            "_order": r["_order"],
        }
        for r in rows
    ]


def compute_cascade_shifts(existing_groups, new_start_minutes, new_end_minutes):
    """
    existing_groups: dict of group_id -> {"start": int minutes, "end": int minutes,
      "order": int} for every OTHER group currently in a Stage 3 day's table (not the
      group that was just inserted) - pass everything, including groups entirely before
      new_start_minutes; those are filtered out internally and never affected.
    new_start_minutes/new_end_minutes: the [start, end) span of the just-inserted item.

    Two-phase cascade:
    Phase 1 (chronological ripple) - walk existing groups in chronological order (by
    current start time) to determine which ones are actually disrupted by the new item,
    and how far the disruption reaches. A group is disrupted if it would otherwise start
    before the "floor" established by everything disrupted before it. This correctly
    finds the disruption's reach - e.g. it might disturb the next two items but leave a
    third alone if there was already enough of a gap to absorb the push - without yet
    deciding the disrupted items' final order.

    Phase 2 (insertion-order layout) - the disrupted set from phase 1 is laid out, back
    to back, starting right after the new item ends, in the order those items were
    originally added to the plan (their "order"), not their original scheduled time.
    Each item keeps its own original duration. This is the behavior actually wanted: if
    two later, distinct items both end up needing to move as a result of one insertion,
    whichever was added to the plan earlier is scheduled first in the resulting gap.

    Returns {group_id: shift_minutes} for every group that needs to move (omitted
    entirely if a group isn't affected).
    """
    candidates = sorted(
        (item for item in existing_groups.items() if item[1]["start"] >= new_start_minutes),
        key=lambda kv: kv[1]["start"],
    )

    floor = new_end_minutes
    affected = []
    for group_id, g in candidates:
        if g["start"] < floor:
            affected.append(group_id)
            floor += g["end"] - g["start"]
        else:
            break  # ripple absorbed here - candidates are time-sorted, so nothing later needs to move either

    affected_by_insertion_order = sorted(affected, key=lambda gid: existing_groups[gid]["order"])
    shifts = {}
    floor = new_end_minutes
    for group_id in affected_by_insertion_order:
        g = existing_groups[group_id]
        duration = g["end"] - g["start"]
        new_group_start = floor
        shifts[group_id] = new_group_start - g["start"]
        floor = new_group_start + duration

    return shifts

    