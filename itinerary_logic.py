"""
Stage 1 itinerary logic: builds a Day x (Morning/Afternoon/Evening) grid for the whole
plan duration, with the arrival and departure entries auto-placed into the correct day
and time slot. Kept free of any Streamlit imports so this can be tested directly.
"""
import json
from datetime import timedelta


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


def classify_time_slot(t, time_slots):
    """Returns the slot name (e.g. 'Morning') that time t falls into, per the
    time_slots definition from rules.json. 'end': '24:00' is treated as end-of-day."""
    minutes = _time_to_minutes(t)
    for slot in time_slots:
        start_h, start_m = map(int, slot["start"].split(":"))
        start_minutes = start_h * 60 + start_m
        if slot["end"] == "24:00":
            end_minutes = 24 * 60
        else:
            end_h, end_m = map(int, slot["end"].split(":"))
            end_minutes = end_h * 60 + end_m
        if start_minutes <= minutes < end_minutes:
            return slot["name"]
    return time_slots[-1]["name"] if time_slots else "Unknown"


def build_stage1_grid(
    plan_name, start_date, end_date,
    arrival_airport, arrival_time,
    departure_airport, departure_time,
    program_location, time_slots,
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
    Arrival entry is placed on start_date, in the slot matching arrival_time.
    Departure entry is placed on end_date, in the slot matching departure_time.
    If start_date == end_date, both entries land on the same day (and the same cell,
    joined on separate lines, if their times fall in the same slot).

    arrival_airport / departure_airport are plain strings - the caller (the page) is
    responsible for resolving them from the selected Program via get_program(), since a
    program can have more than one airport option (e.g. Kaziranga via Guwahati inbound,
    Jorhat outbound).
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

    def add_entry(target_date, text):
        for day in days:
            if day["date"] == target_date:
                slot = classify_time_slot(entry_time, time_slots)
                existing = day[slot]
                day[slot] = f"{existing}\n{text}" if existing else text
                return

    entry_time = arrival_time
    add_entry(start_date, f"Arrival: {arrival_airport}, {arrival_time.strftime('%H:%M')}")

    entry_time = departure_time
    add_entry(end_date, f"Departure: {departure_airport}, {departure_time.strftime('%H:%M')}")

    return {"plan_name": plan_name, "program_location": program_location, "days": days}