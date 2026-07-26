from datetime import date, time

import pandas as pd
import streamlit as st

from itinerary_logic import (
    build_stage1_grid, build_stage3_timeline, compute_addable_slots, get_program, load_rules,
)

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
        arrival_time = st.time_input("Expected Flight Arrival Time *", value=time(11, 30))
    with col_departure:
        departure_time = st.time_input("Expected Flight Departure Time *", value=time(16, 0))

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
        missing.append("Expected Flight Arrival Time")
    if not departure_time:
        missing.append("Expected Flight Departure Time")

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
        arrival_travel_minutes=airport_by_name[arrival_airport],
        departure_airport=departure_airport, departure_time=departure_time,
        departure_travel_minutes=airport_by_name[departure_airport],
        program_location=program_location,
        time_slots=time_slots,
        buffer_rules=rules_data["buffer_rules"],
        transfer_rules=rules_data["transfer_rules"],
        meal_rules=rules_data["meal_rules"],
    )
    # Stored in session_state (not just local variables) so they survive the reruns
    # triggered by the Stage 2 dialog below - generating a fresh plan always replaces
    # whatever was there before, including any activities already added.
    st.session_state["stage1_grid"] = grid
    st.session_state["stage1_boundary"] = {
        "start_date": start_date, "end_date": end_date,
        "arrival_time": arrival_time, "departure_time": departure_time,
    }
    st.session_state["stage2_activities"] = []
    st.session_state["accommodation_details"] = ""
    st.session_state.pop("stage3_df", None)
    st.session_state.pop("stage3_editor", None)
    st.session_state.pop("stage3_rows", None)
    st.session_state["stage3_next_id"] = 0

if "stage1_grid" in st.session_state:
    grid = st.session_state["stage1_grid"]

    st.subheader(grid["plan_name"] or "(Untitled Plan)")
    st.caption(f"Location: {grid['program_location'] or '—'}")

    st.subheader("Stage 2")

    accommodation_details = st.text_input(
        "Accommodation Details *",
        value=st.session_state.get("accommodation_details", ""),
        placeholder="e.g. Nirvana Shillong, Laitumkhrah",
        help="Used to label the 'transfer back to accommodation' rows in Stage 3.",
    )
    st.session_state["accommodation_details"] = accommodation_details

    boundary = st.session_state["stage1_boundary"]
    addable = compute_addable_slots(
        grid["days"], grid["locked_slots"],
        boundary["start_date"], boundary["end_date"],
        boundary["arrival_time"], boundary["departure_time"],
        time_slots,
    )
    meal_rules = rules_data["meal_rules"]
    meal_names = [m["name"] for m in meal_rules]
    meal_duration_by_name = {m["name"]: m["duration_minutes"] for m in meal_rules}

    @st.dialog("Add to Itinerary")
    def add_activity_dialog(day_label, day_date, slot_name):
        st.write(f"**{day_label} — {slot_name}**")
        kind = st.radio("Type", ["Activity", "Meal"], horizontal=True)

        if kind == "Meal":
            name = st.selectbox("Meal", meal_names)
            duration_minutes = st.number_input(
                "Duration (minutes) *", min_value=0, value=meal_duration_by_name.get(name, 30), step=5
            )
        else:
            name = st.text_input("Activity Name *")
            duration_minutes = st.number_input("Duration (minutes) *", min_value=0, value=60, step=15)

        transfer_required = st.checkbox("Transfer required?")
        transfer_minutes = None
        if transfer_required:
            transfer_minutes = st.number_input("Transfer time (minutes) *", min_value=0, value=30, step=15)

        if st.button("Save"):
            if not name:
                st.warning(f"{'Activity Name' if kind == 'Activity' else 'Meal'} is required.")
            elif transfer_required and not transfer_minutes:
                st.warning("Enter a transfer time, or uncheck 'Transfer required'.")
            else:
                entry_text = f"[{kind}] {name} ({duration_minutes} min)"
                if transfer_required:
                    entry_text += f" | Transfer: {transfer_minutes} min"
                for d in st.session_state["stage1_grid"]["days"]:
                    if d["date"] == day_date:
                        existing = d[slot_name]
                        d[slot_name] = f"{existing}\n{entry_text}" if existing else entry_text
                        break
                st.session_state["stage2_activities"].append({
                    "date": day_date, "slot": slot_name,
                    "order": len(st.session_state["stage2_activities"]),
                    "kind": kind, "name": name, "duration_minutes": duration_minutes,
                    "transfer_required": transfer_required, "transfer_minutes": transfer_minutes,
                })
                st.rerun()

    # Header row
    header_cols = st.columns([1.3, 1, 1, 1])
    for col, label in zip(header_cols, ["Day", "Morning", "Afternoon", "Evening"]):
        col.markdown(f"**{label}**")

    for d in grid["days"]:
        row_cols = st.columns([1.3, 1, 1, 1], border=True)
        row_cols[0].markdown(f"**{d['label']}**")

        for col, slot_name in zip(row_cols[1:], ["Morning", "Afternoon", "Evening"]):
            key = (d["date"], slot_name)
            content = d[slot_name]
            with col:
                if content:
                    # Markdown hard line break (trailing double space) so multi-entry
                    # cells (e.g. two stacked activities) wrap and break lines correctly.
                    st.markdown(content.replace("\n", "  \n"))
                if key in grid["locked_slots"]:
                    st.caption("🔒 locked")
                elif key in addable:
                    if st.button("➕ Add", key=f"add_{d['date']}_{slot_name}"):
                        add_activity_dialog(d["label"], d["date"], slot_name)
                elif not content:
                    st.caption("—")

    st.caption(
        "🔒 = arrival/transfer/assembly/check-in/departure, locked and never editable. "
        "➕ = open for activities or meals. Regenerating the plan above resets everything, including added activities."
    )

    df = pd.DataFrame([
        {"Day": d["label"], "Morning": d["Morning"], "Afternoon": d["Afternoon"], "Evening": d["Evening"]}
        for d in grid["days"]
    ])
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("Download Stage 2 as CSV", csv, "plan_grid.csv", "text/csv")

    st.divider()
    if st.button("🕒 Generate Timewise Itinerary"):
        if "timed_events" not in grid:
            st.error(
                "This plan was generated before Stage 3 support was added. Click "
                "'Generate Plan Grid' above to rebuild it, then try again."
            )
        elif not accommodation_details:
            st.error("Accommodation Details is required before generating the timewise itinerary.")
        else:
            rows = build_stage3_timeline(
                grid["timed_events"], st.session_state.get("stage2_activities", []),
                meal_rules, rules_data["default_slot_starts"],
                accommodation_details=accommodation_details,
            )
            st.session_state["stage3_next_id"] = 0

            def _new_row_id():
                st.session_state["stage3_next_id"] += 1
                return st.session_state["stage3_next_id"]

            st.session_state["stage3_rows"] = [{**r, "_id": _new_row_id()} for r in rows]
            # Drop any per-row widget state left over from a previous Stage 3 generation,
            # so old rows' Time/Activity/etc. values can't leak into new rows that happen
            # to reuse the same _id counter values.
            for k in list(st.session_state.keys()):
                if k.startswith(("date_", "time_", "activity_", "type_", "notes_")):
                    del st.session_state[k]

    if "stage3_rows" in st.session_state:
        st.subheader("Stage 3: Timewise Itinerary")
        st.caption(
            "Dates can't be changed - pick from the plan's existing dates only. Everything else "
            "is editable. Click ➕ next to a row to insert a new row directly after it - not just "
            "at the end. Editing a Time shifts every later row that same day by the same amount, "
            "so the rest of the day's schedule stays consistent instead of going stale."
        )

        valid_dates = [d["date"].strftime("%d %b %Y") for d in grid["days"]]

        def _parse_time_safe(s):
            try:
                h, m = map(int, str(s).strip().split(":"))
                return h * 60 + m
            except (ValueError, AttributeError):
                return None

        def _new_row_id():
            st.session_state["stage3_next_id"] += 1
            return st.session_state["stage3_next_id"]

        rows_list = st.session_state["stage3_rows"]

        header_cols = st.columns([1.3, 0.8, 2.3, 1.1, 2, 0.4, 0.4])
        for col, label in zip(header_cols, ["Date", "Time", "Activity", "Type", "Notes", "", ""]):
            if label:
                col.markdown(f"**{label}**")

        insert_after_idx = None
        delete_idx = None

        for idx, row in enumerate(rows_list):
            rid = row["_id"]
            cols = st.columns([1.3, 0.8, 2.3, 1.1, 2, 0.4, 0.4])
            date_idx = valid_dates.index(row["Date"]) if row["Date"] in valid_dates else 0
            with cols[0]:
                st.selectbox("Date", valid_dates, index=date_idx, key=f"date_{rid}", label_visibility="collapsed")
            with cols[1]:
                st.text_input("Time", value=row["Time"], key=f"time_{rid}", label_visibility="collapsed")
            with cols[2]:
                st.text_input("Activity", value=row["Activity"], key=f"activity_{rid}", label_visibility="collapsed")
            with cols[3]:
                st.text_input("Type", value=row["Type"], key=f"type_{rid}", label_visibility="collapsed")
            with cols[4]:
                st.text_input("Notes", value=row["Notes"], key=f"notes_{rid}", label_visibility="collapsed")
            with cols[5]:
                if st.button("➕", key=f"insert_{rid}", help="Insert a new row after this one"):
                    insert_after_idx = idx
            with cols[6]:
                if st.button("🗑️", key=f"delete_{rid}", help="Delete this row"):
                    delete_idx = idx

        # Read back the live widget values (Streamlit tracks these via session_state once
        # rendered with a key) and detect any Time change, to drive the cascade - compared
        # against the stored rows_list, which reflects the state as of the last completed run.
        cascade_deltas = {}  # idx -> delta minutes, for rows whose Time changed this run
        for idx, row in enumerate(rows_list):
            rid = row["_id"]
            new_time = _parse_time_safe(st.session_state.get(f"time_{rid}", row["Time"]))
            old_time = _parse_time_safe(row["Time"])
            if new_time is not None and old_time is not None and new_time != old_time:
                cascade_deltas[idx] = new_time - old_time

        # Sync every row's plain field values from the widgets (Date/Activity/Type/Notes,
        # and Time itself before any cascade adjustment below).
        for idx, row in enumerate(rows_list):
            rid = row["_id"]
            row["Date"] = st.session_state.get(f"date_{rid}", row["Date"])
            row["Time"] = st.session_state.get(f"time_{rid}", row["Time"])
            row["Activity"] = st.session_state.get(f"activity_{rid}", row["Activity"])
            row["Type"] = st.session_state.get(f"type_{rid}", row["Type"])
            row["Notes"] = st.session_state.get(f"notes_{rid}", row["Notes"])

        needs_rerun = False

        if cascade_deltas:
            # Apply each detected delta to every later-in-list row on the same date -
            # "later" means later in this explicit list order, matching how rows are now
            # positioned (via insert-after), not a re-derived time sort.
            for edited_idx, delta in cascade_deltas.items():
                edited_date = rows_list[edited_idx]["Date"]
                for later_idx in range(edited_idx + 1, len(rows_list)):
                    if rows_list[later_idx]["Date"] != edited_date:
                        continue
                    t = _parse_time_safe(rows_list[later_idx]["Time"])
                    if t is None:
                        continue
                    shifted = max(0, min(23 * 60 + 59, t + delta))
                    new_time_str = f"{shifted // 60:02d}:{shifted % 60:02d}"
                    rows_list[later_idx]["Time"] = new_time_str
                    st.session_state[f"time_{rows_list[later_idx]['_id']}"] = new_time_str
            needs_rerun = True

        if insert_after_idx is not None:
            new_row = {
                "_id": _new_row_id(),
                "Date": rows_list[insert_after_idx]["Date"],
                "Time": "", "Activity": "", "Type": "Activity", "Notes": "",
            }
            rows_list.insert(insert_after_idx + 1, new_row)
            needs_rerun = True

        if delete_idx is not None:
            removed = rows_list.pop(delete_idx)
            for prefix in ("date_", "time_", "activity_", "type_", "notes_"):
                st.session_state.pop(f"{prefix}{removed['_id']}", None)
            needs_rerun = True

        st.session_state["stage3_rows"] = rows_list

        if needs_rerun:
            st.rerun()

        export_df = pd.DataFrame([
            {"Date": r["Date"], "Time": r["Time"], "Activity": r["Activity"], "Type": r["Type"], "Notes": r["Notes"]}
            for r in rows_list
        ])
        csv3 = export_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download Stage 3 as CSV", csv3, "timewise_itinerary.csv", "text/csv")
        