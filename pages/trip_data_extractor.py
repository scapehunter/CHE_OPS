import csv
import io
from datetime import date

import pandas as pd
import streamlit as st

st.title("🧳 Trip Data Extractor")
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


def compute_ages(series):
    """Returns (ages, valid_count, invalid_count). Handles both a column pandas has
    already auto-parsed as real dates (typical when reading Excel, since Excel stores
    dates as actual date values, not text) and a column of literal "DD/MM/YYYY"
    strings (typical when reading CSV). Anything that doesn't parse as a valid date
    in the required format is treated as invalid, not silently guessed at."""
    if pd.api.types.is_datetime64_any_dtype(series):
        parsed = series
    else:
        parsed = pd.to_datetime(series, format="%d/%m/%Y", errors="coerce")
    today = pd.Timestamp(date.today())
    ages = []
    for dob in parsed:
        if pd.isna(dob):
            ages.append(None)
        else:
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            ages.append(age)
    return ages, int(parsed.notna().sum()), int(parsed.isna().sum())


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

analysis_cols = st.columns(3)

with analysis_cols[0]:
    st.caption("Gender")
    if gender_col:
        gender_counts = df[gender_col].fillna("(blank)").astype(str).str.strip().value_counts()
        st.dataframe(
            pd.DataFrame({"Gender": gender_counts.index, "Count": gender_counts.values}),
            use_container_width=True, hide_index=True,
        )
        combined_parts.append(pd.DataFrame({"Gender": gender_counts.index, "Count": gender_counts.values}))
    else:
        st.caption("Header not found")

with analysis_cols[1]:
    st.caption("Age Groups (as of today)")
    if dob_col:
        ages, valid_count, invalid_count = compute_ages(df[dob_col])
        if invalid_count:
            st.caption(f"{invalid_count}/{len(df)} DOB values not in DD/MM/YYYY - excluded")
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


if combined_parts:
    combined_df = pd.concat(combined_parts, ignore_index=True)
    combined_csv = combined_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "📥 Download Analysed Data",
        combined_csv, "timewise_itinerary_combined.csv", "text/csv",
    )


