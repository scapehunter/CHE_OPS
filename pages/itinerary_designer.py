from datetime import date, time

import pandas as pd
import streamlit as st

from itinerary_logic import build_stage1_grid, load_rules

st.title("🗺️ Itinerary Designer")
st.write("**Stage 1: Trip skeleton** — enter arrival/departure details to lay out the day-by-day plan grid.")

rules_data = load_rules("rules.json")
time_slots = rules_data["time_slots"]

with st.form("stage1_form"):
    plan_name = st.text_input("Plan Name")
    col_dates1, col_dates2 = st.columns(2)
    with col_dates1:
        start_date = st.date_input("Start Date *", value=date.today())
    with col_dates2:
        end_date = st.date_input("End Date *", value=date.today())

    st.divider()
    col_arrival, col_departure = st.columns(2)
    with col_arrival:
        st.subheader("Arrival")
        arrival_flight_number = st.text_input("Flight Number", key="arrival_flight")
        arrival_airport = st.text_input("Arrival Airport *")
        arrival_time = st.time_input("Expected Arrival Time *", value=time(11, 30))
    with col_departure:
        st.subheader("Departure")
        departure_flight_number = st.text_input("Flight Number", key="departure_flight")
        departure_airport = st.text_input("Departure Airport *")
        departure_time = st.time_input("Expected Departure Time *", value=time(16, 0))

    st.divider()
    program_location = st.text_input("Program Location")

    st.caption("* Mandatory fields")
    submitted = st.form_submit_button("Generate Plan Grid")

if submitted:
    missing = []
    if not start_date:
        missing.append("Start Date")
    if not end_date:
        missing.append("End Date")
    if not arrival_airport:
        missing.append("Arrival Airport")
    if not departure_airport:
        missing.append("Departure Airport")
    if not arrival_time:
        missing.append("Expected Arrival Time")
    if not departure_time:
        missing.append("Expected Departure Time")

    if missing:
        st.error("Missing mandatory field(s): " + ", ".join(missing))
        st.stop()

    if end_date < start_date:
        st.error("End Date can't be before Start Date.")
        st.stop()

    grid = build_stage1_grid(
        plan_name=plan_name,
        start_date=start_date, end_date=end_date,
        arrival_airport=arrival_airport, arrival_time=arrival_time,
        arrival_flight_number=arrival_flight_number or None,
        departure_airport=departure_airport, departure_time=departure_time,
        departure_flight_number=departure_flight_number or None,
        program_location=program_location,
        time_slots=time_slots,
    )

    st.subheader(grid["plan_name"] or "(Untitled Plan)")
    st.caption(f"Location: {grid['program_location'] or '—'}")

    df = pd.DataFrame([
        {"Day": d["label"], "Morning": d["Morning"], "Afternoon": d["Afternoon"], "Evening": d["Evening"]}
        for d in grid["days"]
    ])
    st.dataframe(df, use_container_width=True, hide_index=True)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("Download as CSV", csv, "plan_grid.csv", "text/csv")