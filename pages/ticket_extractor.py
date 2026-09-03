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
st.write("Upload one PDF ticket at a time, so you can review its result before moving to the next.")

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


def read_master_file(uploaded_file):
    """
    Returns (tracking_rows, warning). tracking_rows is a list of dicts, one per
    master entry: {"Name", "Gender", "_key" (normalized name for matching),
    "_gender_norm" (normalized gender), "Status", "Matched File", "Matched PNR",
    "Gender Check"} - starts with every entry "Not Found", updated in place as
    tickets get processed. warning is a string to display if something about the
    file looks off (wrong shape, duplicate names), or None if everything's clean.
    """
    uploaded_file.seek(0)
    name = uploaded_file.name.lower()
    try:
        if name.endswith(".csv"):
            master_df = pd.read_csv(uploaded_file)
        else:
            master_df = pd.read_excel(uploaded_file)
    except Exception as e:
        return [], f"Couldn't read the Master file: {e}"

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
            "Status": "Not Found", "Matched File": "", "Matched PNR": "", "Gender Check": "",
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
    master_identity = (master_file.name, master_file.size)
    if st.session_state.get("master_identity") != master_identity:
        # A new (or newly re-uploaded) Master file - start tracking fresh rather
        # than keeping stale match state from a previous file.
        tracking_rows, master_warning = read_master_file(master_file)
        st.session_state["master_tracking"] = tracking_rows
        st.session_state["master_identity"] = master_identity
        st.session_state["master_warning"] = master_warning

    # Shown every run, not just the one where the file was first processed - a
    # real problem with the master file (e.g. wrong column names) needs to stay
    # visible, not disappear the moment any other widget on the page is touched
    # (like uploading a ticket), even though the underlying problem persists.
    if st.session_state.get("master_warning"):
        st.warning(st.session_state["master_warning"])

    if st.session_state.get("master_tracking"):
        st.caption(f"Master file loaded - {len(st.session_state['master_tracking'])} name(s) being tracked.")
        if st.button("Reset Master Tracking"):
            for entry in st.session_state["master_tracking"]:
                entry["Status"], entry["Matched File"], entry["Matched PNR"], entry["Gender Check"] = "Not Found", "", "", ""


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


uploaded_file = st.file_uploader("Upload Ticket", type=["pdf"], accept_multiple_files=False)

if uploaded_file:
    with st.spinner("Extracting passenger data from PDF..."):
        rows, warning = extract_from_pdf(uploaded_file, trip_type)

    if warning:
        st.warning(warning)

    if not rows:
        st.error("Error: Could not extract valid data from the uploaded PDF. "
                  "Ensure text is selectable (not a scanned image).")
    else:
        st.success(f"Extracted {len(rows)} passenger row(s) from {uploaded_file.name}.")
        column_order = ["File Name", "PNR", "Name", "Gender", "Sector",
                         "Flight Number", "Return Sector", "Return Flight Number"]
        df = pd.DataFrame(rows)[column_order]

        if st.session_state.get("master_tracking"):
            for _, row in df.iterrows():
                key = normalize_name(row["Name"])
                for entry in st.session_state["master_tracking"]:
                    if entry["_key"] == key:
                        if entry["Status"] == "Matched":
                            # Same master name matched by more than one ticket -
                            # note it rather than silently overwriting the first match.
                            entry["Status"] = "Matched (duplicate ticket)"
                        else:
                            entry["Status"] = "Matched"
                        entry["Matched File"] = row["File Name"]
                        entry["Matched PNR"] = row["PNR"]
                        extracted_gender = normalize_gender(row["Gender"])
                        entry["Gender Check"] = "✅ Match" if extracted_gender == entry["_gender_norm"] else "❌ Mismatch"
                        break

        st.dataframe(df, use_container_width=True)

        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("Download as CSV", csv, "ticket_data.csv", "text/csv")

if st.session_state.get("master_tracking"):
    st.divider()
    st.subheader("Master List Tracking")
    st.caption("Updates as each ticket is uploaded - reflects every ticket processed so far this session.")
    tracking_df = pd.DataFrame(st.session_state["master_tracking"])[
        ["Name", "Gender", "Status", "Matched File", "Matched PNR", "Gender Check"]
    ]
    matched_count = (tracking_df["Status"] != "Not Found").sum()
    st.caption(f"{matched_count} / {len(tracking_df)} matched")
    st.dataframe(tracking_df, use_container_width=True, hide_index=True)

    tracking_csv = tracking_df.to_csv(index=False).encode("utf-8")
    st.download_button("Download Master Tracking as CSV", tracking_csv, "master_tracking.csv", "text/csv")
    