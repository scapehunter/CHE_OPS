import pandas as pd
import streamlit as st

st.title("🔍 Compare Datasets")
st.write("Upload two datasets (CSV or Excel), match them on a common column, and see them merged side by side.")


def read_dataset(uploaded_file):
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file)
    return pd.read_excel(uploaded_file)


col_a, col_b = st.columns(2)
with col_a:
    st.subheader("Dataset A")
    file_a = st.file_uploader("Upload file A", type=["csv", "xlsx", "xls"], key="file_a")
with col_b:
    st.subheader("Dataset B")
    file_b = st.file_uploader("Upload file B", type=["csv", "xlsx", "xls"], key="file_b")

if not (file_a and file_b):
    st.info("Upload both datasets to continue.")
    st.stop()

try:
    df_a = read_dataset(file_a)
    df_b = read_dataset(file_b)
except Exception as e:
    st.error(f"Couldn't read one of the files: {e}")
    st.stop()

with col_a:
    st.caption(f"{len(df_a)} rows, {len(df_a.columns)} columns")
    st.dataframe(df_a.head(), use_container_width=True)
with col_b:
    st.caption(f"{len(df_b)} rows, {len(df_b.columns)} columns")
    st.dataframe(df_b.head(), use_container_width=True)

st.divider()
st.subheader("Match settings")

key_col1, key_col2, key_col3 = st.columns(3)
with key_col1:
    cols_a = list(df_a.columns)
    default_key_a = 0
    key_a = st.selectbox("Key column in Dataset A", cols_a, index=default_key_a)
with key_col2:
    cols_b = list(df_b.columns)
    # default to the same column name in B if it exists, else the first column
    default_key_b = cols_b.index(key_a) if key_a in cols_b else 0
    key_b = st.selectbox("Key column in Dataset B", cols_b, index=default_key_b)
with key_col3:
    how = st.selectbox(
        "Rows to include",
        ["Outer (show everything, including mismatches)", "Inner (only rows present in both)",
         "Left (all of A, matching from B)", "Right (all of B, matching from A)"],
        index=0,
    )
how_map = {
    "Outer (show everything, including mismatches)": "outer",
    "Inner (only rows present in both)": "inner",
    "Left (all of A, matching from B)": "left",
    "Right (all of B, matching from A)": "right",
}

st.subheader("Columns to compare")
pick_col1, pick_col2 = st.columns(2)
with pick_col1:
    other_cols_a = [c for c in cols_a if c != key_a]
    selected_a = st.multiselect("From Dataset A", other_cols_a, default=other_cols_a)
with pick_col2:
    other_cols_b = [c for c in cols_b if c != key_b]
    selected_b = st.multiselect("From Dataset B", other_cols_b, default=other_cols_b)

# Normalize the key values so matching isn't broken by whitespace or case differences
# (a common source of false "only in A"/"only in B" rows when comparing rosters).
normalize_keys = st.checkbox(
    "Ignore case/whitespace differences in the key column when matching", value=True
)

col_ignore_check, col_ignore_chars = st.columns([1, 2])
with col_ignore_check:
    ignore_chars_enabled = st.checkbox(
        "Also ignore specific character(s)",
        help="Useful when the same key is formatted differently between files - e.g. "
             "'Doe, John' in one file vs 'Doe John' in the other (a comma separator "
             "difference). Enter every character to strip out before matching.",
    )
with col_ignore_chars:
    ignore_chars = st.text_input(
        "Character(s) to ignore", value=",", disabled=not ignore_chars_enabled,
        label_visibility="collapsed", placeholder="e.g. , or -",
    )

a_subset = df_a[[key_a] + selected_a].copy()
b_subset = df_b[[key_b] + selected_b].copy()

if normalize_keys or ignore_chars_enabled:
    a_match = a_subset[key_a].astype(str)
    b_match = b_subset[key_b].astype(str)
    if normalize_keys:
        a_match = a_match.str.strip().str.upper()
        b_match = b_match.str.strip().str.upper()
    if ignore_chars_enabled and ignore_chars:
        # Strip every character in the given string literally (not as a regex
        # pattern), so something like "." or "*" is treated as itself, not a
        # wildcard - avoids needing to explain regex-escaping to someone who just
        # wants to ignore a comma or a hyphen.
        for ch in ignore_chars:
            a_match = a_match.str.replace(ch, "", regex=False)
            b_match = b_match.str.replace(ch, "", regex=False)
        if normalize_keys:
            # A stray extra space can appear once the separator itself is removed
            # (e.g. "Doe, John" -> "Doe John" is fine, but "Doe,John" with no
            # space stays fine too) - re-strip only if case/whitespace normalization
            # was already requested, so this doesn't change behavior for someone
            # who only wants exact-character-stripping with everything else intact.
            a_match = a_match.str.strip()
            b_match = b_match.str.strip()
    a_subset["_match_key"] = a_match
    b_subset["_match_key"] = b_match
    left_on, right_on = "_match_key", "_match_key"
else:
    left_on, right_on = key_a, key_b

merged = a_subset.merge(
    b_subset, left_on=left_on, right_on=right_on,
    how=how_map[how], suffixes=(" (A)", " (B)"), indicator=True,
)
if (normalize_keys or ignore_chars_enabled) and "_match_key" in merged.columns:
    merged = merged.drop(columns=["_match_key"])

merged["_merge"] = merged["_merge"].map({
    "left_only": "Only in Dataset A", "right_only": "Only in Dataset B", "both": "In Both",
})
merged = merged.rename(columns={"_merge": "Match Status"})

# Columns that exist in both selections got suffixed (" (A)"/" (B)") by the merge - flag
# where those pairs disagree, since that's usually the whole point of a comparison tool.
shared_cols = set(selected_a) & set(selected_b)
diff_flags = []
for col in shared_cols:
    col_a_name, col_b_name = f"{col} (A)", f"{col} (B)"
    if col_a_name in merged.columns and col_b_name in merged.columns:
        flag_name = f"{col}: match?"
        merged[flag_name] = merged[col_a_name].astype(str) == merged[col_b_name].astype(str)
        diff_flags.append(flag_name)

st.divider()
st.subheader("Result")

status_counts = merged["Match Status"].value_counts()
summary_cols = st.columns(len(status_counts) + (len(diff_flags) if diff_flags else 0))
i = 0
for status, count in status_counts.items():
    summary_cols[i].metric(status, count)
    i += 1
for flag in diff_flags:
    mismatches = int((~merged[flag]).sum())
    summary_cols[i].metric(f"{flag.replace(': match?', '')} mismatches", mismatches)
    i += 1


def highlight_row(row):
    styles = [""] * len(row)
    if row["Match Status"] != "In Both":
        styles = ["background-color: #fff3cd"] * len(row)
    for j, col in enumerate(row.index):
        if col in diff_flags and not row[col]:
            styles[j] = "background-color: #f8d7da"
    return styles


styled = merged.style.apply(highlight_row, axis=1)
st.dataframe(styled, use_container_width=True)

csv = merged.to_csv(index=False).encode("utf-8")
st.download_button("Download comparison as CSV", csv, "comparison.csv", "text/csv")