import csv
import io
from datetime import date

import pandas as pd
import streamlit as st

st.title("🧳 Participant Data Extractor")
st.write(
    "Upload a trip data file (CSV or Excel) to see it laid out clearly, check it against "
    "the expected registration headers, and get a quick breakdown of the participants."
)

EXPECTED_HEADERS = {
    "General": [
        "Sr No",
        "Title  (Mr/ Ms/ Mrs)",
        "Student Name",
        "Gender",
        "Date of Birth(DD/MM/YYYY)",
        "Nationality",
        "ID No",
        "ID Type(Aadhar / Passport)",
        "Meal Preference  (Veg/Non Veg/Jain)",
        "Type",
    ],
    "Medical Details": [
        "Food allergies",
        "Other allergies",
        "Specific Medical condition (if any)",
        "Present Medication (if any)",
    ],
    "Insurance Details": [
        "Nominee Name ",
        "Nominee Relation",
        "Nominee Date of Birth",
    ],
}
# This is the ONE fixed expected header set this tool checks against - not something
# a person configures per upload. If the expected headers ever change, this is the
# single place to update them.

AGE_BRACKETS = [
    (0, 9, "Under 10"),
    (10, 12, "10-12"),
    (13, 15, "13-15"),
    (16, 18, "16-18"),
    (19, None, "19+"),
]
# Not something the person specified explicitly - a reasonable default grouping for a
# school-trip context (younger students through older teens, plus adults/chaperones
# in the open-ended 19+ bucket). Easy to adjust here if a different grouping is wanted.

TYPE_ALIASES = {
    "Student": ["student"],
    "Teacher": ["teacher"],
    "CHE/Trip Leader/CHTL": ["che", "trip leader", "chtl", "che/trip leader/chtl"],
}
# CHE, Trip Leader, and CHTL are different names for the SAME role, not three
# distinct types - any of these (or the combined label itself) canonicalizes to
# "CHE/Trip Leader/CHTL". This is the one source of truth for recognized Type
# values - used both to flag anything that doesn't match any alias, and everywhere
# else Type gets matched (age group filter, gender cross-tab, room allocation), so
# a typo'd or differently-worded Type value shows up as a visible flag instead of
# just silently vanishing from every downstream feature.


def canonicalize_type(value):
    """Returns the canonical type name (a TYPE_ALIASES key) for a raw Type value,
    matching against any of its known aliases after whitespace/case normalization,
    or None if the value doesn't match any known type at all."""
    normalized = " ".join(str(value).split()).strip().lower()
    for canonical, aliases in TYPE_ALIASES.items():
        if normalized in aliases:
            return canonical
    return None


def normalize_header(s):
    """Collapses any run of whitespace to a single space and strips ends, so an
    incidental double-space or trailing space in the file doesn't register as a
    missing header when the wording otherwise matches exactly."""
    return " ".join(str(s).split())


def find_matching_column(expected_header, actual_columns):
    """Returns the actual column name in the file that matches expected_header after
    whitespace normalization, or None if nothing matches."""
    target = normalize_header(expected_header)
    for col in actual_columns:
        if normalize_header(col) == target:
            return col
    return None


CANDIDATE_DOB_FORMATS = ["%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y", "%Y-%m-%d", "%Y/%m/%d"]
# Tried in this order when detecting the DOB column's actual format - DD/MM/YYYY
# first, since that's what the header itself specifies, but a file that's actually
# in a different format (dashes instead of slashes, ISO order, etc.) still parses
# correctly rather than getting every row wrongly rejected.


def detect_dob_format(series, candidate_formats=CANDIDATE_DOB_FORMATS):
    """
    Tries each candidate format against the (string) DOB values, returns
    (format, successful_parse_count) for whichever format parses the most values
    correctly. This is a whole-column decision, not per-row guessing - important
    for genuinely ambiguous dates like "03/04/2012" (could be 3 April or 4 March),
    since a column with even a few unambiguous entries (a day > 12, ruling out
    month-first) will correctly favor the right format for the whole column rather
    than guessing row by row.
    """
    str_series = series.astype(str)
    best_format, best_count = candidate_formats[0], -1
    for fmt in candidate_formats:
        parsed = pd.to_datetime(str_series, format=fmt, errors="coerce")
        count = int(parsed.notna().sum())
        if count > best_count:
            best_count, best_format = count, fmt
    return best_format, best_count


def compute_ages(series):
    """Returns (ages, valid_count, invalid_count, detected_format). Handles both a
    column pandas has already auto-parsed as real dates (typical when reading Excel,
    since Excel stores dates as actual date values, not text - detected_format is
    None in this case, there's no string format to report) and a column of date
    strings in whatever format the data actually uses, auto-detected rather than
    assumed. Anything that doesn't parse under the detected format is treated as
    invalid, not silently guessed at."""
    if pd.api.types.is_datetime64_any_dtype(series):
        parsed = series
        detected_format = None
    else:
        detected_format, _ = detect_dob_format(series)
        parsed = pd.to_datetime(series.astype(str), format=detected_format, errors="coerce")
    today = pd.Timestamp(date.today())
    ages = []
    for dob in parsed:
        if pd.isna(dob):
            ages.append(None)
        else:
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            ages.append(age)
    return ages, int(parsed.notna().sum()), int(parsed.isna().sum()), detected_format


def bracket_for_age(age):
    for lo, hi, label in AGE_BRACKETS:
        if age >= lo and (hi is None or age <= hi):
            return label
    return "Unknown"


def read_dataset_raw(uploaded_file):
    """
    Reads the file with no header interpretation at all - every row comes back as
    plain data. Used only to build the preview a person picks their header row from.

    For CSV specifically, this deliberately avoids pandas' normal CSV parser: it
    enforces a consistent column count inferred from the first row, which fails
    outright on a file with e.g. a single-cell title row followed by much wider data
    rows - not a misread, a hard crash. Reading via the csv module and padding
    ragged rows to the widest row's length sidesteps that entirely.
    """
    uploaded_file.seek(0)
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        text = uploaded_file.read().decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
        max_cols = max((len(r) for r in rows), default=0)
        padded = [r + [""] * (max_cols - len(r)) for r in rows]
        return pd.DataFrame(padded)
    return pd.read_excel(uploaded_file, header=None)


def read_dataset(uploaded_file, header_row):
    """Re-reads the same file, telling pandas which row (0-indexed) is the real
    header - everything before that row is dropped, not just skipped."""
    uploaded_file.seek(0)
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file, header=header_row)
    return pd.read_excel(uploaded_file, header=header_row)


uploaded_file = st.file_uploader("Upload trip data", type=["csv", "xlsx", "xls"], accept_multiple_files=False)

if not uploaded_file:
    st.info("Upload a file to continue.")
    st.stop()

try:
    raw_df = read_dataset_raw(uploaded_file)
except Exception as e:
    st.error(f"Couldn't read the file: {e}")
    st.stop()

st.caption("Enter which row number is the real header - the preview below updates once you do.")
header_row = st.number_input(
    "Header row *", min_value=0, max_value=max(len(raw_df) - 1, 0),
    value=None, placeholder="Enter a row number", key="header_row",
)

df = None
if header_row is None:
    st.dataframe(raw_df.head(10), use_container_width=True)
else:
    try:
        df = read_dataset(uploaded_file, header_row)
    except Exception as e:
        st.error(f"Couldn't parse with that header row: {e}")

if df is None:
    st.info("Enter a valid header row number to continue.")
    st.stop()

combined_parts = []
st.divider()
st.subheader("Data")
st.caption(f"{len(df)} rows, {len(df.columns)} columns")
st.dataframe(df, use_container_width=True)

st.divider()
st.subheader("Header Check")

column_lookup = {}  # expected header -> actual matching column name in df, or None
for header in [h for headers in EXPECTED_HEADERS.values() for h in headers]:
    column_lookup[header] = find_matching_column(header, df.columns)

HEADER_GRID_COLUMNS = {"General": 3, "Medical Details": 4, "Insurance Details": 3}
# Column counts requested per category, so each grid reads as a compact block rather
# than one header per row - General's 9 headers form a 3x3 grid, Medical's 4 headers
# fit a single row of 4, Insurance's 3 headers fit a single row of 3.

for category, headers in EXPECTED_HEADERS.items():
    st.caption(category)
    num_cols = HEADER_GRID_COLUMNS.get(category, 3)
    grid_cols = st.columns(num_cols)
    for i, header in enumerate(headers):
        status = "✅" if column_lookup[header] else "❌"
        with grid_cols[i % num_cols]:
            st.markdown(f"{status} {header}")

st.divider()
st.subheader("Data Analysis")
st.markdown(f"**Total number of records:** {len(df)}")

gender_col = column_lookup.get("Gender")
dob_col = column_lookup.get("Date of Birth(DD/MM/YYYY)")
meal_col = column_lookup.get("Meal Preference  (Veg/Non Veg/Jain)")
type_col = column_lookup.get("Type")

canonical_type_col = None
if type_col:
    canonical_type_col = df[type_col].apply(canonicalize_type)
    is_known = canonical_type_col.notna()
    if (~is_known).any():
        unrecognized = df.loc[~is_known, type_col].astype(str).str.strip()
        unrecognized_counts = unrecognized.value_counts()
        st.warning(
            "Some rows have a Type value that doesn't match Student, Teacher, or any "
            "of CHE/Trip Leader/CHTL. They're excluded from Student Age Group and Room "
            "Allocation (both need a recognized type to work), but will still appear "
            "as their own row in the Gender breakdown below:\n\n"
            + "\n".join(f"- \"{val}\" — {count} row(s)" for val, count in unrecognized_counts.items())
        )

analysis_cols = st.columns(3)

with analysis_cols[0]:
    st.caption("Gender")
    if gender_col:
        gender_series = df[gender_col].fillna("(blank)").astype(str).str.strip()
        gender_series.name = "Gender"
        if type_col:
            type_series = df[type_col].fillna("(blank)").astype(str).str.strip()
            type_series.name = "Type"
            gender_cross = pd.crosstab(type_series, gender_series).reset_index()
            st.dataframe(gender_cross, use_container_width=True, hide_index=True)
            combined_parts.append(gender_cross)
        else:
            gender_counts = gender_series.value_counts()
            st.dataframe(
                pd.DataFrame({"Gender": gender_counts.index, "Count": gender_counts.values}),
                use_container_width=True, hide_index=True,
            )
            combined_parts.append(pd.DataFrame({"Gender": gender_counts.index, "Count": gender_counts.values}))
    else:
        st.caption("Header not found")

with analysis_cols[1]:
    st.caption("Student Age Group")
    if dob_col:
        if type_col:
            is_student = canonical_type_col == "Student"
            dob_series = df.loc[is_student, dob_col]
        else:
            dob_series = df[dob_col]
        ages, valid_count, invalid_count, detected_format = compute_ages(dob_series)
        FORMAT_DISPLAY_NAMES = {
            "%d/%m/%Y": "DD/MM/YYYY", "%d-%m-%Y": "DD-MM-YYYY", "%d.%m.%Y": "DD.MM.YYYY",
            "%m/%d/%Y": "MM/DD/YYYY", "%Y-%m-%d": "YYYY-MM-DD", "%Y/%m/%d": "YYYY/MM/DD",
        }
        if detected_format:
            st.caption(f"Detected date format: {FORMAT_DISPLAY_NAMES.get(detected_format, detected_format)}")
        if invalid_count:
            st.caption(f"{invalid_count}/{len(dob_series)} DOB values didn't match the detected format - excluded")
        bracket_counts = {}
        for age in ages:
            if age is None:
                continue
            label = bracket_for_age(age)
            bracket_counts[label] = bracket_counts.get(label, 0) + 1
        bracket_order = [b[2] for b in AGE_BRACKETS]
        age_table = pd.DataFrame({
            "Age Group": bracket_order,
            "Count": [bracket_counts.get(label, 0) for label in bracket_order],
        })
        combined_parts.append(pd.DataFrame({
            "Age Group": bracket_order,
            "Count": [bracket_counts.get(label, 0) for label in bracket_order],
        }))
        st.dataframe(age_table, use_container_width=True, hide_index=True)
    else:
        st.caption("Header not found")

with analysis_cols[2]:
    st.caption("Meal Preference")
    if meal_col:
        meal_counts = df[meal_col].fillna("(blank)").astype(str).str.strip().value_counts()
        st.dataframe(
            pd.DataFrame({"Meal Preference": meal_counts.index, "Count": meal_counts.values}),
            use_container_width=True, hide_index=True,
        )
        combined_parts.append(pd.DataFrame({"Meal Preference": meal_counts.index, "Count": meal_counts.values}))
    else:
        st.caption("Header not found")


st.caption("Food Allergies")

# Drops NaN values, strips hidden spaces, and removes completely blank text rows
filtered_df = df[df["Food allergies"].notna() & (df["Food allergies"].astype(str).str.strip() != "") & (df["Food allergies"].astype(str).str.lower().str.strip() != "na") & (df["Food allergies"].astype(str).str.lower().str.strip() != "no")]

if filtered_df.empty:
    st.info("🎉 No food allergies reported for these students.")
else:
    st.dataframe(filtered_df[["Student Name", "Food allergies"]])
    combined_parts.append(filtered_df[["Student Name", "Food allergies"]])



st.caption("Other Allergies")

# Drops NaN values, strips hidden spaces, and removes completely blank text rows
filtered_df = df[df["Other allergies"].notna() & (df["Other allergies"].astype(str).str.strip() != "") & (df["Other allergies"].astype(str).str.lower().str.strip() != "na") & (df["Other allergies"].astype(str).str.lower().str.strip() != "no")]
if filtered_df.empty:
    st.info("🎉 No Other allergies reported for these students.")
else:
    st.dataframe(filtered_df[["Student Name", "Other allergies"]])
    combined_parts.append(filtered_df[["Student Name", "Other allergies"]])

st.caption("Specific Medical condition (if any)")

# Drops NaN values, strips hidden spaces, and removes completely blank text rows
spec_medical_cols = [col for col in df.columns if col.strip().startswith("Specific Medical condition")]
filtered_df = df[df[spec_medical_cols[0]].notna() & (df[spec_medical_cols[0]].astype(str).str.strip() != "") & (df[spec_medical_cols[0]].astype(str).str.lower().str.strip() != "na") & (df[spec_medical_cols[0]].astype(str).str.lower().str.strip() != "no")]
if filtered_df.empty:
    st.info("🎉 No Specific Medical condition reported for these students.")
else:
    st.dataframe(filtered_df[["Student Name", spec_medical_cols[0]]])
    combined_parts.append(filtered_df[["Student Name", spec_medical_cols[0]]])


st.caption("Present Medication")

# Drops NaN values, strips hidden spaces, and removes completely blank text rows
spec_medical_cols = [col for col in df.columns if col.strip().startswith("Present Medication")]
filtered_df = df[df[spec_medical_cols[0]].notna() & (df[spec_medical_cols[0]].astype(str).str.strip() != "") & (df[spec_medical_cols[0]].astype(str).str.lower().str.strip() != "na") & (df[spec_medical_cols[0]].astype(str).str.lower().str.strip() != "no")]
if filtered_df.empty:
    st.info("🎉 No student is presently on medication.")
else:
    st.dataframe(filtered_df[["Student Name", spec_medical_cols[0]]])
    combined_parts.append(filtered_df[["Student Name", spec_medical_cols[0]]])


def allocate_rooms(count, max_size, min_size):
    """
    Returns {room_size: room_count} for splitting `count` people into rooms sized
    between min_size and max_size (inclusive), using only max_size and min_size
    themselves wherever a clean combination of the two exists.

    Searches for the largest number of max_size rooms (a) such that the remaining
    people can be split evenly across some number of min_size-or-(min_size+1)-sized
    rooms, all within [min_size, max_size]. This is a real search across every
    possible room count, not just "fill with max, patch the one leftover room" -
    that simpler approach can miss the actual best split entirely: 33 people at
    Max 4/Min 3 needs 6 Quads + 3 Triples (33 = 24+9), but greedily filling with
    Quads first leaves only 1 person over after 8 Quads, which can't be fixed by
    only adjusting the last room - the correct split needs 2 fewer Quads to free
    up enough people to form clean Triples instead.
    """
    if count <= 0 or max_size < min_size or min_size < 1:
        return {}

    for a in range(count // max_size, -1, -1):
        remainder = count - a * max_size
        if remainder == 0:
            return {max_size: a} if a else {}
        min_k = -(-remainder // max_size)  # fewest rooms that could hold the remainder
        max_k = remainder // min_size if min_size else 0  # most rooms possible at min_size each
        for k in range(max(min_k, 1), max_k + 1):
            base, extra = divmod(remainder, k)
            smallest, largest = base, (base + 1 if extra else base)
            if smallest >= min_size and largest <= max_size:
                result = {max_size: a} if a else {}
                if extra:
                    result[base + 1] = result.get(base + 1, 0) + extra
                if k - extra:
                    result[base] = result.get(base, 0) + (k - extra)
                return result

    # No clean combination exists at all (a genuinely impossible split, e.g. 5
    # people at Max 4/Min 3) - fall back to one room sized to fit everyone rather
    # than losing anyone, even though it violates the stated min/max.
    return {count: 1}


ROOM_SIZE_NAMES = {1: "Single", 2: "Double", 3: "Triple", 4: "Quad", 5: "Quint", 6: "Hex"}

st.divider()
st.subheader("Room Allocation")
st.caption(
    "Rooms are allocated gender-wise within each type - only males share a room "
    "with each other, and only females share a room with each other, across all "
    "three participant types."
)

room_input_cols = st.columns(3)
room_settings = {}
for col, participant_type in zip(room_input_cols, ["Students", "Teacher", "CHE/Trip Leader/ CHTL"]):
    with col:
        st.markdown(f"**{participant_type}**")
        max_size = st.number_input(f"Max in a Room ({participant_type})", min_value=1, value=3, key=f"max_{participant_type}")
        min_size = st.number_input(f"Min in a Room ({participant_type})", min_value=1, max_value=max_size, value=min(2, max_size), key=f"min_{participant_type}")
        room_settings[participant_type] = (max_size, min_size)

if st.button("Allocate Room"):
    if not (gender_col and type_col):
        st.warning("Both the Gender and Type headers are needed to allocate rooms.")
    else:
        gender_norm = df[gender_col].astype(str).str.strip().str.lower()
        type_canonical = canonical_type_col

        # (row label, canonical type to match, room settings key, gender to match)
        groups = [
            ("Boys", "Student", "Students", "male"),
            ("Girls", "Student", "Students", "female"),
            ("Male Teachers", "Teacher", "Teacher", "male"),
            ("Female Teachers", "Teacher", "Teacher", "female"),
            ("Male CHTL", "CHE/Trip Leader/CHTL", "CHE/Trip Leader/ CHTL", "male"),
            ("Female CHTL", "CHE/Trip Leader/CHTL", "CHE/Trip Leader/ CHTL", "female"),
        ]

        all_sizes_used = set()
        row_results = []
        for label, type_match, settings_key, gender_match in groups:
            count = int(((type_canonical == type_match) & (gender_norm == gender_match)).sum())
            max_size, min_size = room_settings[settings_key]
            allocation = allocate_rooms(count, max_size, min_size) if count else {}
            all_sizes_used.update(allocation.keys())
            row_results.append({"label": label, "count": count, "allocation": allocation})

        size_order = sorted(all_sizes_used)
        size_columns = [ROOM_SIZE_NAMES.get(s, str(s)) for s in size_order]

        table_rows = []
        total_pax = 0
        total_rooms_by_size = {s: 0 for s in size_order}
        for r in row_results:
            row = {"": r["label"], "Number": r["count"]}
            room_total = 0
            for s, col_name in zip(size_order, size_columns):
                n_rooms = r["allocation"].get(s, 0)
                row[col_name] = n_rooms if n_rooms else ""
                room_total += n_rooms
                total_rooms_by_size[s] += n_rooms
            row["Total"] = room_total
            table_rows.append(row)
            total_pax += r["count"]

        total_row = {"": "Total Pax", "Number": total_pax}
        for s, col_name in zip(size_order, size_columns):
            total_row[col_name] = total_rooms_by_size[s]
        total_row["Total"] = sum(total_rooms_by_size.values())
        table_rows.append(total_row)

        room_table = pd.DataFrame(table_rows)
        st.session_state["room_allocation_table"] = room_table

if "room_allocation_table" in st.session_state:
    st.dataframe(st.session_state["room_allocation_table"], use_container_width=True, hide_index=True)
    combined_parts.append(st.session_state["room_allocation_table"])


if combined_parts:
    combined_df = pd.concat(combined_parts, ignore_index=True)
    combined_csv = combined_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "📥 Download Analysed Data",
        combined_csv, "analysed_data.csv", "text/csv",
    )