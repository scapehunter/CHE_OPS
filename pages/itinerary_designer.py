from datetime import date, time, datetime

import pandas as pd
import streamlit as st

from itinerary_logic import (
    build_stage1_grid, build_stage3_timeline, compute_addable_slots, compute_cascade_shifts,
    expand_activity_or_meal, fill_missing_meals, get_program, load_rules,
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
        meal_stop_required = False
        meal_stop_minutes = None
        if transfer_required:
            transfer_minutes = st.number_input("Transfer time (minutes) *", min_value=0, value=30, step=15)
            if kind == "Activity":
                meal_stop_required = st.checkbox("Meal stop needed during this transfer?")
                if meal_stop_required:
                    meal_stop_minutes = st.number_input(
                        "Meal stop duration (minutes) *", min_value=0, value=30, step=5,
                        help="Extends the whole transfer (both ways) by this much - e.g. a 60 min "
                             "transfer with a 30 min meal stop becomes 90 min each way.",
                    )

        if st.button("Save"):
            if not name:
                st.warning(f"{'Activity Name' if kind == 'Activity' else 'Meal'} is required.")
            elif transfer_required and not transfer_minutes:
                st.warning("Enter a transfer time, or uncheck 'Transfer required'.")
            elif meal_stop_required and not meal_stop_minutes:
                st.warning("Enter a meal stop duration, or uncheck 'Meal stop needed'.")
            else:
                entry_text = f"[{kind}] {name} ({duration_minutes} min)"
                if transfer_required:
                    entry_text += f" | Transfer: {transfer_minutes} min"
                    if meal_stop_required:
                        entry_text += f" (+{meal_stop_minutes} min meal stop)"
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
                    "meal_stop_required": meal_stop_required, "meal_stop_minutes": meal_stop_minutes,
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
            arrival_dt = datetime.combine(boundary["start_date"], boundary["arrival_time"])
            departure_dt = datetime.combine(boundary["end_date"], boundary["departure_time"])
            stage2_with_meals = fill_missing_meals(
                st.session_state.get("stage2_activities", []),
                meal_rules, grid["days"], arrival_dt, departure_dt, time_slots,
            )
            rows = build_stage3_timeline(
                grid["timed_events"], stage2_with_meals,
                meal_rules, rules_data["default_slot_starts"],
                accommodation_details=accommodation_details,
            )
            stage3_dfs = {}
            for d in grid["days"]:
                date_label = d["date"].strftime("%d %b %Y")
                day_rows = [
                    {"Time": r["Time"], "Activity": r["Activity"], "Type": r["Type"], "Notes": r["Notes"],
                     "_group": r["_group"], "_order": r["_order"]}
                    for r in rows if r["Date"] == date_label
                ]
                stage3_dfs[d["date"].isoformat()] = pd.DataFrame(
                    day_rows, columns=["Time", "Activity", "Type", "Notes", "_group", "_order"]
                )
            st.session_state["stage3_dfs"] = stage3_dfs
            # Every future Stage 3 insertion needs an _order higher than anything already
            # used, so newly-added items always rank as "added most recently" for the
            # cascade's insertion-order tie-break.
            all_orders = [r["_order"] for r in rows] or [0]
            all_groups = [r["_group"] for r in rows] or [-1]
            st.session_state["stage3_order_counter"] = max(all_orders) + 1
            st.session_state["stage3_group_counter"] = max(all_groups) + 1
            # Clear any per-day widget/dialog/pending-shift state left over from a
            # previous generation.
            for k in list(st.session_state.keys()):
                if k.startswith(("stage3_editor_", "pending_shift_", "show_insert_dialog_")):
                    del st.session_state[k]

    if "stage3_dfs" in st.session_state:
        st.subheader("Stage 3: Timewise Itinerary")
        st.caption(
            "One table per day - dates never appear as an editable field since each table "
            "already belongs to a single day. Everything else is directly editable, "
            "worksheet-style. Use ➕ Insert Row to add a row at a precise position (not just "
            "the end); the table's own row-delete (select a row) removes one. Editing a Time "
            "does NOT automatically shift later rows in that day - an explicit prompt appears "
            "below that day's table after you make the edit."
        )

        def _parse_time_safe(s):
            try:
                h, m = map(int, str(s).strip().split(":"))
                return h * 60 + m
            except (ValueError, AttributeError):
                return None

        for d in grid["days"]:
            date_iso = d["date"].isoformat()
            date_label = d["date"].strftime("%d %b %Y")
            if date_iso not in st.session_state["stage3_dfs"]:
                continue

            st.markdown(f"#### {d['label']}")

            editor_key = f"stage3_editor_{date_iso}"
            insert_flag_key = f"show_insert_dialog_{date_iso}"
            pending_key = f"pending_shift_{date_iso}"

            col_insert, col_download = st.columns([1, 3])
            with col_insert:
                if st.button("➕ Insert Row", key=f"insert_btn_{date_iso}"):
                    st.session_state[insert_flag_key] = True

            current_df = st.session_state["stage3_dfs"][date_iso]

            @st.dialog(f"Insert Row — {date_label}")
            def insert_row_dialog(date_iso=date_iso, day_date=d["date"], editor_key=editor_key, insert_flag_key=insert_flag_key):
                kind = st.radio("Type", ["Activity", "Meal"], horizontal=True)
                if kind == "Meal":
                    name = st.selectbox("Meal", meal_names)
                    duration_minutes = st.number_input(
                        "Duration (minutes) *", min_value=0, value=meal_duration_by_name.get(name, 30), step=5
                    )
                else:
                    name = st.text_input("Activity Name *")
                    duration_minutes = st.number_input("Duration (minutes) *", min_value=0, value=60, step=15)

                anchor_time_str = st.text_input(
                    "Time *", placeholder="HH:MM",
                    help="This determines where the row lands in the table - rows are always in time order.",
                )

                transfer_required = st.checkbox("Transfer required?")
                transfer_minutes = None
                meal_stop_required = False
                meal_stop_minutes = None
                if transfer_required:
                    transfer_minutes = st.number_input("Transfer time (minutes) *", min_value=0, value=30, step=15)
                    if kind == "Activity":
                        meal_stop_required = st.checkbox("Meal stop needed during this transfer?")
                        if meal_stop_required:
                            meal_stop_minutes = st.number_input(
                                "Meal stop duration (minutes) *", min_value=0, value=30, step=5,
                                help="Extends the whole transfer (both ways) by this much.",
                            )

                if st.button("Insert"):
                    parsed_time = None
                    if anchor_time_str:
                        try:
                            hh, mm = map(int, anchor_time_str.strip().split(":"))
                            parsed_time = time(hh, mm)
                        except (ValueError, TypeError):
                            parsed_time = None

                    if not name:
                        st.warning(f"{'Activity Name' if kind == 'Activity' else 'Meal'} is required.")
                    elif parsed_time is None:
                        st.warning("Enter a valid Time (HH:MM).")
                    elif transfer_required and not transfer_minutes:
                        st.warning("Enter a transfer time, or uncheck 'Transfer required'.")
                    elif meal_stop_required and not meal_stop_minutes:
                        st.warning("Enter a meal stop duration, or uncheck 'Meal stop needed'.")
                    else:
                        anchor_dt = datetime.combine(day_date, parsed_time)
                        new_rows, end_dt = expand_activity_or_meal(
                            anchor_dt, day_date, kind, name, duration_minutes,
                            transfer_required, transfer_minutes,
                            meal_stop_required, meal_stop_minutes,
                            meal_rules, accommodation_details,
                        )
                        new_group = st.session_state["stage3_group_counter"]
                        new_order = st.session_state["stage3_order_counter"]
                        st.session_state["stage3_group_counter"] += 1
                        st.session_state["stage3_order_counter"] += 1
                        new_rows_formatted = pd.DataFrame([
                            {"Time": r["dt"].strftime("%H:%M"), "Activity": r["Activity"],
                             "Type": r["Type"], "Notes": r["Notes"], "_group": new_group, "_order": new_order}
                            for r in new_rows
                        ])

                        df2 = st.session_state["stage3_dfs"][date_iso].copy()
                        anchor_minutes = parsed_time.hour * 60 + parsed_time.minute
                        end_minutes = end_dt.hour * 60 + end_dt.minute

                        # Reconstruct every OTHER existing group's [start, end] span and
                        # insertion order from the pre-merge table, then run the two-phase
                        # cascade: a chronological ripple finds which groups are actually
                        # disrupted and how far the disruption reaches (e.g. it might stop
                        # before a later item that already had enough of a gap); the
                        # disrupted set is then laid out by insertion order, not original
                        # time - matching how a person expects repeatedly-bumped items to
                        # settle (whichever was added to the plan earlier goes first).
                        existing_groups = {}
                        for gid, group_df in df2.groupby("_group"):
                            times = [_parse_time_safe(t) for t in group_df["Time"]]
                            times = [t for t in times if t is not None]
                            if not times:
                                continue
                            existing_groups[gid] = {
                                "start": min(times), "end": max(times),
                                "order": group_df["_order"].iloc[0],
                            }

                        group_shifts = compute_cascade_shifts(
                            existing_groups, anchor_minutes, end_minutes,
                            buffer_minutes=rules_data.get("cascade_buffer_minutes", 5),
                        )
                        # Applied immediately, not stored as a pending action - inserting
                        # a row always leaves the day's table internally consistent on
                        # its own, no separate confirmation click needed.
                        for row_idx in df2.index:
                            gid = df2.loc[row_idx, "_group"]
                            if gid in group_shifts:
                                t = _parse_time_safe(df2.loc[row_idx, "Time"])
                                if t is not None:
                                    shifted = max(0, min(23 * 60 + 59, t + group_shifts[gid]))
                                    df2.loc[row_idx, "Time"] = f"{shifted // 60:02d}:{shifted % 60:02d}"

                        combined = pd.concat([df2, new_rows_formatted], ignore_index=True)
                        combined["_sort_key"] = combined["Time"].apply(_parse_time_safe)
                        # Rows with an unparseable time (shouldn't normally happen here,
                        # since this dialog always produces valid HH:MM) sort last rather
                        # than crashing or silently vanishing.
                        combined["_sort_key"] = combined["_sort_key"].fillna(24 * 60)
                        combined = combined.sort_values("_sort_key", kind="stable").drop(columns="_sort_key").reset_index(drop=True)

                        st.session_state["stage3_dfs"][date_iso] = combined
                        st.session_state.pop(editor_key, None)
                        st.session_state[insert_flag_key] = False
                        st.rerun()

            if st.session_state.get(insert_flag_key):
                insert_row_dialog()

            edited_df = st.data_editor(
                current_df,
                key=editor_key,
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "Time": st.column_config.TextColumn("Time", help="24-hour HH:MM"),
                    "_group": {"hidden": True},
                    "_order": {"hidden": True},
                },
            )

            # A row added via the table's own native "+" (not the Insert Row dialog)
            # won't have a _group/_order yet - give it fresh ones so future cascades
            # still know how to treat it (as its own single-row group, added just now).
            missing_meta = edited_df["_group"].isna()
            if missing_meta.any():
                for idx in edited_df.index[missing_meta]:
                    edited_df.loc[idx, "_group"] = st.session_state["stage3_group_counter"]
                    edited_df.loc[idx, "_order"] = st.session_state["stage3_order_counter"]
                    st.session_state["stage3_group_counter"] += 1
                    st.session_state["stage3_order_counter"] += 1

            shift_candidate = None
            for i in current_df.index:
                if i not in edited_df.index:
                    continue
                old_t = _parse_time_safe(current_df.loc[i, "Time"])
                new_t = _parse_time_safe(edited_df.loc[i, "Time"])
                if old_t is not None and new_t is not None and new_t != old_t:
                    shift_candidate = {"row_index": i, "delta": new_t - old_t,
                                        "activity": edited_df.loc[i, "Activity"]}
                    break

            st.session_state["stage3_dfs"][date_iso] = edited_df

            if shift_candidate:
                st.session_state[pending_key] = shift_candidate

            pending = st.session_state.get(pending_key)
            if pending:
                sign = "+" if pending["delta"] > 0 else ""
                st.info(
                    f"Time for **{pending['activity']}** changed by {sign}{pending['delta']} min. "
                    f"Apply the same shift to every later row on {date_label}?"
                )

            col_apply, col_dismiss = st.columns([1, 1])
            with col_apply:
                if st.button("⏩ Time check and Adjust", key=f"apply_{date_iso}"):
                    if pending:
                        df3 = st.session_state["stage3_dfs"][date_iso].copy()
                        if "group_shifts" in pending:
                            # From an insert: each disrupted group shifts by its own
                            # computed amount (from compute_cascade_shifts), preserving
                            # that group's internal structure exactly.
                            group_shifts = pending["group_shifts"]
                            for j in df3.index:
                                gid = df3.loc[j, "_group"]
                                if gid not in group_shifts:
                                    continue
                                t = _parse_time_safe(df3.loc[j, "Time"])
                                if t is None:
                                    continue
                                shifted = max(0, min(23 * 60 + 59, t + group_shifts[gid]))
                                df3.loc[j, "Time"] = f"{shifted // 60:02d}:{shifted % 60:02d}"
                        elif "threshold_minutes" in pending:
                            # From an older-style insert detection: shift every row
                            # at/after the threshold, except the newly inserted rows.
                            delta = pending["delta"]
                            threshold = pending["threshold_minutes"]
                            exclude_keys = {tuple(k) for k in pending.get("exclude_keys", [])}
                            for j in df3.index:
                                t = _parse_time_safe(df3.loc[j, "Time"])
                                if t is None or t < threshold:
                                    continue
                                if (df3.loc[j, "Time"], df3.loc[j, "Activity"]) in exclude_keys:
                                    continue
                                shifted = max(0, min(23 * 60 + 59, t + delta))
                                df3.loc[j, "Time"] = f"{shifted // 60:02d}:{shifted % 60:02d}"
                        else:
                            # From an edit: shift every row positioned after the edited one.
                            delta = pending["delta"]
                            row_idx = pending["row_index"]
                            for j in df3.index:
                                if j <= row_idx:
                                    continue
                                t = _parse_time_safe(df3.loc[j, "Time"])
                                if t is None:
                                    continue
                                shifted = max(0, min(23 * 60 + 59, t + delta))
                                df3.loc[j, "Time"] = f"{shifted // 60:02d}:{shifted % 60:02d}"
                        # Re-sort so display order stays chronologically correct - a shift
                        # can otherwise leave rows visually out of order even though every
                        # individual time is right (e.g. a shifted row now sitting later
                        # than a row that was originally after it but wasn't shifted).
                        df3["_sort_key"] = df3["Time"].apply(_parse_time_safe)
                        df3["_sort_key"] = df3["_sort_key"].fillna(24 * 60)
                        df3 = df3.sort_values("_sort_key", kind="stable").drop(columns="_sort_key").reset_index(drop=True)

                        st.session_state["stage3_dfs"][date_iso] = df3
                        st.session_state.pop(pending_key, None)
                        st.session_state.pop(editor_key, None)
                        st.rerun()
                    # else: nothing pending - clicking does nothing, on purpose.
            with col_dismiss:
                if pending and st.button("Dismiss", key=f"dismiss_{date_iso}"):
                    st.session_state.pop(pending_key, None)
                    st.rerun()

            with col_download:
                day_csv = st.session_state["stage3_dfs"][date_iso].drop(
                    columns=["_group", "_order"]
                ).to_csv(index=False).encode("utf-8")
                st.download_button(
                    f"Download {date_label} as CSV", day_csv, f"itinerary_{date_iso}.csv",
                    "text/csv", key=f"dl_{date_iso}",
                )

            st.divider()

        combined_parts = []
        for d in grid["days"]:
            date_iso = d["date"].isoformat()
            if date_iso in st.session_state["stage3_dfs"]:
                day_df = st.session_state["stage3_dfs"][date_iso].drop(columns=["_group", "_order"]).copy()
                day_df.insert(0, "Date", d["date"].strftime("%d %b %Y"))
                combined_parts.append(day_df)
        if combined_parts:
            combined_df = pd.concat(combined_parts, ignore_index=True)
            combined_csv = combined_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "📥 Download Combined Timewise Itinerary (all days)",
                combined_csv, "timewise_itinerary_combined.csv", "text/csv",
            )