"""
Stage 1 itinerary logic: travel-day (assembly -> flight -> road transfer -> hotel)
row generation. Kept free of any Streamlit imports so the date/time math can be
tested directly.
"""
import json
from datetime import datetime, timedelta


def load_rules(path="rules.json"):
    with open(path) as f:
        data = json.load(f)
    return data["buffer_rules"]


def _apply_rule(rule, anchors):
    anchor_time = anchors.get(rule["anchor"])
    if anchor_time is None:
        return None
    return anchor_time + timedelta(minutes=rule["offset_minutes"])


def build_stage1_itinerary(
    trip_date, flight_departure_time, flight_arrival_time,
    flight_number, destination_airport, hotel_location,
    transfer_duration_minutes, rules, transfer_distance_km=None,
):
    """
    trip_date: date
    flight_departure_time, flight_arrival_time: time (both assumed same trip_date;
        if arrival clock-time is earlier than departure, it's treated as landing the
        next day - a simple way to handle overnight flights without a separate input)
    transfer_duration_minutes: int - the road-transfer duration, as entered by the person.
    transfer_distance_km: optional, purely informational - included in the Notes column
        if given, has no effect on the computed times.
    rules: list of rule dicts as loaded from rules.json

    Returns a list of row dicts with keys: Date, Time, Activity, Type, Notes,
    already sorted into chronological order.
    """
    departure_dt = datetime.combine(trip_date, flight_departure_time)
    arrival_dt = datetime.combine(trip_date, flight_arrival_time)
    if arrival_dt < departure_dt:
        arrival_dt += timedelta(days=1)

    anchors = {"flight_departure": departure_dt, "flight_arrival": arrival_dt}

    rows = []
    transfer_start = None
    for rule in rules:
        t = _apply_rule(rule, anchors)
        if t is None:
            continue
        rows.append({"_dt": t, "Activity": rule["label"], "Type": rule["type"], "Notes": ""})
        if rule["id"] == "depart_for_hotel":
            transfer_start = t

    rows.append({
        "_dt": departure_dt,
        "Activity": f"Board flight {flight_number} to {destination_airport}",
        "Type": "Flight", "Notes": "",
    })
    rows.append({
        "_dt": arrival_dt,
        "Activity": f"Arrival at {destination_airport}",
        "Type": "Arrival", "Notes": "",
    })

    if transfer_start is None:
        # No "depart_for_hotel" rule found in rules.json - fall back to a 60-minute
        # post-arrival buffer so the itinerary can still be built.
        transfer_start = arrival_dt + timedelta(minutes=60)

    hotel_arrival_dt = transfer_start + timedelta(minutes=transfer_duration_minutes)
    note = f"Road transfer ~{transfer_duration_minutes} min"
    if transfer_distance_km is not None:
        note += f" (~{transfer_distance_km} km)"
    rows.append({
        "_dt": hotel_arrival_dt,
        "Activity": f"Arrival at {hotel_location}",
        "Type": "Hotel Check-in",
        "Notes": note,
    })

    rows.sort(key=lambda r: r["_dt"])
    for r in rows:
        r["Date"] = r["_dt"].strftime("%d %b %Y")
        r["Time"] = r["_dt"].strftime("%H:%M")
        del r["_dt"]

    return rows
