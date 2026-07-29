import csv
import io

import pandas as pd
import streamlit as st

st.title("🧳 Trip Data Extractor")
st.write(
    "Upload a trip data file (CSV or Excel) to see it laid out clearly. "
    "This is the first step of the tool - showing the data meaningfully, before any "
    "extraction logic is added on top."
)


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

st.divider()
st.subheader("Data")
st.caption(f"{len(df)} rows, {len(df.columns)} columns")
st.dataframe(df, use_container_width=True)

st.subheader("Column overview")
overview_rows = []
for col in df.columns:
    non_null = df[col].notna().sum()
    overview_rows.append({
        "Column": col,
        "Type": str(df[col].dtype),
        "Non-empty values": f"{non_null} / {len(df)}",
        "Unique values": df[col].nunique(),
    })
st.dataframe(pd.DataFrame(overview_rows), use_container_width=True, hide_index=True)

csv_bytes = df.to_csv(index=False).encode("utf-8")
st.download_button("Download as CSV", csv_bytes, "trip_data.csv", "text/csv")
