from datetime import date, time

import pandas as pd
import streamlit as st

from itinerary_logic import build_stage1_grid, get_program, load_rules

st.title("🗺️ Itinerary Designer")
st.write("**Stage 1: Trip skeleton** — enter arrival/departure details to lay out the day-by-day plan grid.")

rules_data = load_rules("rules.json")
time_slots = rules_data["time_slots"]
programs = rules_data.get("programs", [])
program_names = [p["name"] for p in programs]

plan_name = st.text_input("Plan Name")

program_location = st.selectbox(
    "Program Location *", options=program_names,
    help="Airport(s) are populated automatically based on the selected program.",
)
selected_program = get_program(program_location, programs) if program_location else None

# Airport selection needs to happen outside the form, since the choices depend on
# which program was picked, and a form's contents can't react to a widget inside it
# until the whole form is submitted.
arrival_airport = None
departure_airport = None
if selected_program:
    airport_by_name = {a["airport"]: a["travel_time_minutes"] for a in selected_program["airports"]}
    airport_options = list(airport_by_name.keys())

    def format_travel_time(minutes):
        return f"~{minutes // 60}h {minutes % 60}m" if minutes else "time not available"

    if len(airport_options) == 1:
        arrival_airport = departure_airport = airport_options[0]
        st.caption(f"Nearest airport: **{arrival_airport}** ({format_travel_time(airport_by_name[arrival_airport])} to program location)")
    else:
        st.caption("Multiple airport options for this program — pick whichever applies for arrival and departure; travel time updates to match.")
        col_a, col_b = st.columns(2)
        with col_a:
            arrival_airport = st.selectbox("Arrival Airport *", airport_options, key="arrival_airport_choice")
            st.caption(f"Travel time: {format_travel_time(airport_by_name[arrival_airport])}")
        with col_b:
            departure_airport = st.selectbox("Departure Airport *", airport_options, key="departure_airport_choice")
            st.caption(f"Travel time: {format_travel_time(airport_by_name[departure_airport])}")

with st.form("stage1_form"):
    col_dates1, col_dates2 = st.columns(2)
    with col_dates1:
        start_date = st.date_input("Start Date *", value=date.today())
    with col_dates2:
        end_date = st.date_input("End Date *", value=date.today())

    st.divider()
    col_arrival, col_departure = st.columns(2)
    with col_arrival:
        st.subheader("Arrival")
        arrival_time = st.time_input("Expected Arrival Time *", value=time(11, 30))
    with col_departure:
        st.subheader("Departure")
        departure_time = st.time_input("Expected Departure Time *", value=time(16, 0))

    st.caption("* Mandatory fields")
    submitted = st.form_submit_button("Generate Plan Grid")

if submitted:
    missing = []
    if not program_location:
        missing.append("Program Location")
    if not arrival_airport:
        missing.append("Arrival Airport")
    if not departure_airport:
        missing.append("Departure Airport")
    if not start_date:
        missing.append("Start Date")
    if not end_date:
        missing.append("End Date")
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
        departure_airport=departure_airport, departure_time=departure_time,
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