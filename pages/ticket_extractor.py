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

st.title("🎫 Ticket Extractor")
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
        st.dataframe(df, use_container_width=True)

        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("Download as CSV", csv, "ticket_data.csv", "text/csv")