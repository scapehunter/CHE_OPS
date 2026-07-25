from datetime import date, time

import pandas as pd
import streamlit as st

from itinerary_logic import build_stage1_itinerary, load_rules

st.title("🗺️ Itinerary Designer")
st.write("**Stage 1: Travel day** — enter flight and hotel details to auto-generate the timed schedule for arrival day.")

rules = load_rules("rules.json")

with st.form("stage1_form"):
    col1, col2 = st.columns(2)
    with col1:
        trip_date = st.date_input("Trip start date", value=date.today())
        flight_departure_time = st.time_input("Flight departure time", value=time(8, 40))
        flight_arrival_time = st.time_input("Flight arrival time", value=time(11, 30))
        flight_number = st.text_input("Flight number", placeholder="e.g. 6E 123")
    with col2:
        destination_airport = st.text_input("Destination airport", placeholder="e.g. Guwahati Airport")
        hotel_location = st.text_input("Hotel location", placeholder="e.g. Nirvana Shillong")
        transfer_duration_minutes = st.number_input(
            "Road transfer duration (minutes)", min_value=0, value=240, step=15
        )
        transfer_distance_km = st.number_input(
            "Road transfer distance (km) — optional", min_value=0, value=0, step=10
        )

    submitted = st.form_submit_button("Generate Stage 1 itinerary")

if submitted:
    if not (flight_number and destination_airport and hotel_location):
        st.error("Flight number, destination airport, and hotel location are all required.")
        st.stop()

    rows = build_stage1_itinerary(
        trip_date=trip_date,
        flight_departure_time=flight_departure_time,
        flight_arrival_time=flight_arrival_time,
        flight_number=flight_number,
        destination_airport=destination_airport,
        hotel_location=hotel_location,
        transfer_duration_minutes=transfer_duration_minutes,
        transfer_distance_km=transfer_distance_km or None,
        rules=rules,
    )

    df = pd.DataFrame(rows)[["Date", "Time", "Activity", "Type", "Notes"]]
    st.subheader("Generated schedule")
    st.dataframe(df, use_container_width=True)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("Download as CSV", csv, "stage1_itinerary.csv", "text/csv")
