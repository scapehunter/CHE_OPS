import csv
import io
import re
import streamlit as st
import pandas as pd
import pdfplumber

try:
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

from extractor import extract_ticket_data

st.title("🎫 Ticket Extraction & Verification")
st.write("Upload one or more PDF tickets - each is extracted independently, and results build up in one combined table below.")

TRIP_TYPE_OPTIONS = {
    "Auto-detect": "auto",
    "One-way (single flight)": "one_way",
    "One-way, connecting (layover, same direction)": "connecting",
    "Round-trip": "round_trip",
}
trip_type_label = st.radio(
    "Trip type",
    options=list(TRIP_TYPE_OPTIONS.keys()),
    index=0,
    horizontal=True,
    help=(
        "If you already know the shape of the itinerary, select it here instead of "
        "leaving it on Auto-detect. 'One-way' and 'connecting' guarantee no return leg "
        "is ever reported, even if the PDF has stray text that could otherwise be "
        "misread as one. 'Round-trip' will flag a warning if no return leg is actually "
        "found, so a mismatch is visible instead of silently wrong."
    ),
)
trip_type = TRIP_TYPE_OPTIONS[trip_type_label]

st.divider()
st.caption(
    "Optional: upload a Master file (CSV/Excel, exactly two columns - Name and "
    "Gender) to verify each extracted ticket's Name and Gender against it."
)
master_file = st.file_uploader("Upload Master file (optional)", type=["csv", "xlsx", "xls"], key="master_file")


def normalize_name(name):
    """
    Returns a sorted tuple of lowercased name words - not just a whitespace/case-
    collapsed string. Flight tickets very commonly print names as
    SURNAME/GIVENNAME (a standard IATA convention), while a master roster
    naturally has them as "Given Name Surname" - a plain string comparison would
    never match those even though they're the same person. Comparing the *set*
    of words instead makes the match independent of order or a slash separator,
    while still correctly NOT matching two genuinely different names (a typo
    like "Jon" vs "John" stays unmatched, this isn't fuzzy/approximate matching).
    """
    words = str(name).replace("/", " ").split()
    return tuple(sorted(w.lower() for w in words))


def normalize_gender(gender):
    """Maps common gender representations (M/Male/F/Female, any casing) to one of
    'Male'/'Female' for comparison - falls back to the cleaned original text for
    anything else, so an unexpected value still compares consistently rather than
    silently failing to match itself."""
    cleaned = str(gender).strip().upper()
    if cleaned in ("M", "MALE"):
        return "Male"
    if cleaned in ("F", "FEMALE"):
        return "Female"
    return " ".join(str(gender).split()).strip()


def match_rows_against_master(rows):
    """
    Checks each extracted passenger row's Name against the current master
    tracking list, updating matched entries in place (mutates
    st.session_state["master_tracking"] directly, since it's the same list
    object). Shared between processing a newly-uploaded ticket and re-syncing
    after "Reset Master Tracking" is clicked, so both paths behave identically.

    Tracks every file/PNR that matched a given person, not just the most recent
    one - a person legitimately appearing on more than one ticket (separate
    onward and return PDFs, common when they're booked or issued separately) is
    the expected case, not a data problem, so it's shown as a normal match count
    rather than flagged as a "duplicate".
    """
    if not st.session_state.get("master_tracking"):
        return
    for row in rows:
        key = normalize_name(row["Name"])
        for entry in st.session_state["master_tracking"]:
            if entry["_key"] == key:
                matched_files = entry.setdefault("_matched_files", [])
                matched_pnrs = entry.setdefault("_matched_pnrs", [])
                matched_sectors = entry.setdefault("_matched_sectors", [])
                if row["File Name"] not in matched_files:
                    matched_files.append(row["File Name"])
                if row["PNR"] not in matched_pnrs:
                    matched_pnrs.append(row["PNR"])
                if row.get("Sector") and row["Sector"] not in matched_sectors:
                    matched_sectors.append(row["Sector"])

                entry["Matched File"] = ", ".join(matched_files)
                entry["Matched PNR"] = ", ".join(matched_pnrs)
                entry["Sector"] = ", ".join(matched_sectors)
                entry["Status"] = f"Matched ({len(matched_files)} ticket{'s' if len(matched_files) != 1 else ''})"

                extracted_gender = normalize_gender(row["Gender"])
                gender_ok = extracted_gender == entry["_gender_norm"]
                # A genuine mismatch found on any one of the matches stays
                # visible even if a later match looks fine - worth a look either
                # way, rather than letting a later "correct" ticket silently
                # hide an earlier real discrepancy.
                if entry["Gender Check"] != "❌ Mismatch":
                    entry["Gender Check"] = "✅ Match" if gender_ok else "❌ Mismatch"
                break


def read_master_file_raw(uploaded_file):
    """
    Reads the file with no header interpretation at all - every row comes back as
    plain data, used only to build a preview for picking the real header row.

    For CSV specifically, this deliberately avoids pandas' normal CSV parser: it
    enforces a consistent column count inferred from the first row, which fails
    outright on a file with e.g. a blank or title row above the real header (the
    exact shape that produces this master file's "Unnamed: 0/1/2" problem) -
    not a misread, a hard crash. Reading via the csv module and padding ragged
    rows to the widest row's length sidesteps that entirely.
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


def read_master_file_with_header(uploaded_file, header_row):
    """Re-reads the file with the given row (0-indexed) as the real header -
    everything before that row is dropped, not just skipped."""
    uploaded_file.seek(0)
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file, header=header_row)
    return pd.read_excel(uploaded_file, header=header_row)


def build_master_tracking(master_df):
    """
    Returns (tracking_rows, warning) from an already-parsed master dataframe -
    one dict per master entry: {"Name", "Gender", "_key" (normalized name for
    matching), "_gender_norm" (normalized gender), "Status", "Matched File",
    "Matched PNR", "Gender Check"} - starts with every entry "Not Found",
    updated in place as tickets get processed. warning is a string to display
    if something about the file looks off (wrong shape, duplicate names), or
    None if everything's clean.
    """
    cols = {str(c).strip().lower(): c for c in master_df.columns}
    if "name" not in cols or "gender" not in cols:
        return [], (
            f"Master file must have exactly two columns named 'Name' and 'Gender' - "
            f"found: {list(master_df.columns)}"
        )

    name_col, gender_col = cols["name"], cols["gender"]
    tracking_rows = []
    seen_keys = set()
    duplicate_names = []
    for _, row in master_df.iterrows():
        key = normalize_name(row[name_col])
        if not key:
            continue
        if key in seen_keys:
            duplicate_names.append(row[name_col])
            continue  # first occurrence wins
        seen_keys.add(key)
        tracking_rows.append({
            "Name": row[name_col], "Gender": row[gender_col],
            "_key": key, "_gender_norm": normalize_gender(row[gender_col]),
            "Status": "Not Found", "Matched File": "", "Matched PNR": "", "Sector": "", "Gender Check": "",
            "_matched_files": [], "_matched_pnrs": [], "_matched_sectors": [],
        })

    warning = None
    if duplicate_names:
        warning = (
            f"{len(duplicate_names)} duplicate name(s) in the Master file - only the "
            f"first entry for each was used: {', '.join(map(str, duplicate_names[:5]))}"
            + ("..." if len(duplicate_names) > 5 else "")
        )
    return tracking_rows, warning


if master_file:
    try:
        master_raw = read_master_file_raw(master_file)
    except Exception as e:
        master_raw = None
        st.error(f"Couldn't read the Master file: {e}")

    if master_raw is not None:
        st.caption("Enter which row number is the real header for the Master file - the preview below updates once you do.")
        master_header_row = st.number_input(
            "Master file header row *", min_value=0, max_value=max(len(master_raw) - 1, 0),
            value=None, placeholder="Enter a row number", key="master_header_row",
        )

        master_identity = (master_file.name, master_file.size, master_header_row)
        if master_header_row is None:
            st.dataframe(master_raw.head(10), use_container_width=True)
            st.session_state.pop("master_tracking", None)
        else:
            if st.session_state.get("master_identity") != master_identity:
                # A new (or newly re-uploaded, or re-header-picked) Master file -
                # start tracking fresh rather than keeping stale match state.
                try:
                    master_df = read_master_file_with_header(master_file, master_header_row)
                    tracking_rows, master_warning = build_master_tracking(master_df)
                except Exception as e:
                    tracking_rows, master_warning = [], f"Couldn't parse with that header row: {e}"
                st.session_state["master_tracking"] = tracking_rows
                st.session_state["master_identity"] = master_identity
                st.session_state["master_warning"] = master_warning

            # Shown every run, not just the one where the file was first processed
            # - a real problem with the master file (e.g. wrong column names)
            # needs to stay visible, not disappear the moment any other widget on
            # the page is touched (like uploading a ticket), even though the
            # underlying problem persists.
            if st.session_state.get("master_warning"):
                st.warning(st.session_state["master_warning"])

            if st.session_state.get("master_tracking"):
                st.caption(f"Master file loaded - {len(st.session_state['master_tracking'])} name(s) being tracked.")
                if st.button("Reset Master Tracking"):
                    for entry in st.session_state["master_tracking"]:
                        entry["Status"], entry["Matched File"], entry["Matched PNR"], entry["Sector"], entry["Gender Check"] = "Not Found", "", "", "", ""
                        entry["_matched_files"], entry["_matched_pnrs"], entry["_matched_sectors"] = [], [], []
                    for rows in st.session_state.get("ticket_rows_cache", {}).values():
                        match_rows_against_master(rows)
                    st.rerun()


def build_ocr_lookup(pdf, text):
    """
    Only used for the boarding-pass format when the name is missing from the text
    layer entirely. OCRs every page and returns {page_index: full_ocr_text}.
    """
    if not OCR_AVAILABLE:
        return None
    if "Departing Flight" not in text and "PNR/Booking Ref" not in text:
        return None
    if re.search(r"(Mr|Ms|Mrs|Mstr)\s+[A-Za-z][A-Za-z\s]{2,39}?\s+Adult", text, re.IGNORECASE):
        return None  # name already present in text layer, no OCR needed

    lookup = {}
    for i, page in enumerate(pdf.pages):
        try:
            image = page.to_image(resolution=200).original
            lookup[i] = pytesseract.image_to_string(image)
        except Exception:
            continue
    return lookup


def extract_from_pdf(uploaded_file, trip_type):
    try:
        with pdfplumber.open(uploaded_file) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            ocr_lookup = build_ocr_lookup(pdf, text)
    except Exception as e:
        st.error(f"Error reading {uploaded_file.name}: {e}")
        return None, None

    rows, warning = extract_ticket_data(text, ocr_name_lookup=ocr_lookup, trip_type=trip_type)
    if not rows:
        return None, warning

    for row in rows:
        row["File Name"] = uploaded_file.name

    return rows, warning


uploaded_files = st.file_uploader("Upload Ticket(s)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    if "ticket_rows_cache" not in st.session_state:
        st.session_state["ticket_rows_cache"] = {}  # (name, size) -> list of row dicts

    current_identities = [(f.name, f.size) for f in uploaded_files]
    new_files = [f for f in uploaded_files if (f.name, f.size) not in st.session_state["ticket_rows_cache"]]

    if new_files:
        # Only extract files that haven't been processed yet - a file already in
        # the cache is left completely untouched, even though the uploader
        # widget re-returns the full current file list on every rerun. Matching
        # a whole re-accumulated batch against the master list on every rerun
        # would re-flag already-matched files as "duplicate" purely from being
        # reprocessed, not because of an actual duplicate ticket.
        progress = st.progress(0.0, text="Extracting tickets...")
        for i, f in enumerate(new_files):
            rows, warning = extract_from_pdf(f, trip_type)
            if warning:
                st.warning(f"{f.name}: {warning}")
            if not rows:
                st.error(f"{f.name}: Could not extract valid data. Ensure text is selectable (not a scanned image).")
                rows = []
            else:
                match_rows_against_master(rows)
            st.session_state["ticket_rows_cache"][(f.name, f.size)] = rows
            progress.progress((i + 1) / len(new_files), text=f"Extracted {i + 1}/{len(new_files)}")
        progress.empty()

    all_rows = []
    for ident in current_identities:
        all_rows.extend(st.session_state["ticket_rows_cache"].get(ident, []))

    if all_rows:
        st.success(f"Extracted {len(all_rows)} passenger row(s) from {len(current_identities)} ticket(s).")
        column_order = ["File Name", "PNR", "Name", "Gender", "Sector",
                         "Flight Number", "Return Sector", "Return Flight Number"]
        df = pd.DataFrame(all_rows)[column_order]
        st.dataframe(df, use_container_width=True)

        csv_bytes = df.to_csv(index=False).encode("utf-8")
        st.download_button("Download as CSV", csv_bytes, "ticket_data.csv", "text/csv")

if st.session_state.get("master_tracking"):
    st.divider()
    st.subheader("Master List Tracking")
    st.caption("Updates as each ticket is uploaded - reflects every ticket processed so far this session.")
    tracking_df = pd.DataFrame(st.session_state["master_tracking"])[
        ["Name", "Gender", "Status", "Matched File", "Matched PNR", "Sector", "Gender Check"]
    ]
    matched_count = (tracking_df["Status"] != "Not Found").sum()
    st.caption(f"{matched_count} / {len(tracking_df)} matched")
    st.dataframe(tracking_df, use_container_width=True, hide_index=True)

    tracking_csv = tracking_df.to_csv(index=False).encode("utf-8")
    st.download_button("Download Master Tracking as CSV", tracking_csv, "master_tracking.csv", "text/csv")

    