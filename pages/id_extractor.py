import re

import pandas as pd
import pdfplumber
import pytesseract
import streamlit as st
from PIL import Image

st.title("🪪 ID Extractor")
st.write(
    "Upload Aadhaar cards or Passports (PDF, JPG, or PNG) - all of the same type per "
    "batch - to extract Name, Gender, Date of Birth, and ID Number."
)
st.info(
    "OCR on photographed ID documents isn't perfectly reliable - lighting, angle, and "
    "print quality all affect it. Every extracted value below is directly editable in "
    "the table, so review against the actual documents before using the result, "
    "rather than trusting it blindly. Rows with anything it couldn't confidently read "
    "are flagged in the Flag column.",
    icon="ℹ️",
)

id_type = st.radio("ID Type", ["Aadhaar", "Passport"], horizontal=True)

uploaded_files = st.file_uploader(
    "Upload ID documents", type=["pdf", "jpg", "jpeg", "png"], accept_multiple_files=True,
)


def best_rotation_ocr(img):
    """
    Tries all four 90-degree rotations and returns the text/angle for whichever
    produces the highest average per-word OCR confidence. Tesseract's own
    orientation detection (image_to_osd) was tested against real photographed ID
    samples and found unreliable on its own - it picked a materially worse angle
    than this confidence-based approach on more than one real sample. Trying all
    four and comparing actual OCR confidence is slower but empirically correct
    far more often.
    """
    best = None
    for angle in (0, 90, 180, 270):
        rotated = img.rotate(angle, expand=True)
        data = pytesseract.image_to_data(rotated, output_type=pytesseract.Output.DICT)
        confidences = [int(c) for c in data["conf"] if int(c) >= 0]
        avg_conf = sum(confidences) / len(confidences) if confidences else -1
        if best is None or avg_conf > best[2]:
            text = pytesseract.image_to_string(rotated)
            best = (text, angle, avg_conf)
    return best


def ocr_uploaded_file(uploaded_file):
    """Returns (text, angle, confidence) for a PDF (rendered to image first,
    first page only) or an image file directly."""
    uploaded_file.seek(0)
    name = uploaded_file.name.lower()
    if name.endswith(".pdf"):
        with pdfplumber.open(uploaded_file) as pdf:
            img = pdf.pages[0].to_image(resolution=300).original
    else:
        img = Image.open(uploaded_file)
    return best_rotation_ocr(img)


def extract_aadhaar_fields(text):
    """
    Aadhaar cards have no explicit 'Name:' label - the name appears as its own
    line, typically right before the DOB line. DOB and Gender are matched near
    their own labels. The 12-digit Aadhaar number is matched carefully to avoid
    the 16-digit VID (Virtual ID) that appears on newer e-Aadhaar letters -
    requiring exactly 12 digits with no trailing 4th group.
    """
    result = {"Name": "", "Gender": "", "DOB": "", "ID Number": ""}

    dob_match = re.search(r"(?:DOB|Date of Birth)[^\d]{0,15}(\d{2}[/-]\d{2}[/-]\d{4})", text, re.IGNORECASE)
    if dob_match:
        result["DOB"] = dob_match.group(1)

    gender_match = re.search(r"\b(Male|Female)\b", text, re.IGNORECASE)
    if gender_match:
        result["Gender"] = gender_match.group(1).capitalize()

    for m in re.finditer(r"\b(\d{4}\s?\d{4}\s?\d{4})\b(?!\s?\d{4})", text):
        candidate = re.sub(r"\D", "", m.group(1))
        if len(candidate) == 12:
            result["ID Number"] = candidate
            break

    if dob_match:
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if dob_match.group(1) in line:
                for j in range(i - 1, max(i - 3, -1), -1):
                    candidate = lines[j].strip()
                    if re.fullmatch(r"[A-Za-z][A-Za-z\s.]{2,40}", candidate):
                        result["Name"] = candidate
                        break
                if result["Name"]:
                    break
    return result


def extract_passport_fields(text):
    """
    Passports have explicit labels (Surname, Given Name(s), Date of Birth, Sex,
    Passport No.), which is more reliable than Aadhaar's unlabeled name - but OCR
    frequently inserts stray noise characters between a label and its value, and
    sometimes garbles a short label (like "Surname") past matching entirely, or
    collapses two side-by-side fields (Date of Birth / Sex) onto one shared value
    line. All three are handled with fallbacks below, found necessary through
    testing against real photographed passports, not assumed upfront. The
    Passport No. also falls back to the MRZ (machine-readable zone) at the
    bottom of the page if the labeled field itself doesn't match.
    """
    result = {"Name": "", "Gender": "", "DOB": "", "ID Number": ""}

    surname_match = re.search(r"Surname\s*\n?[^A-Za-z\n]{0,4}([A-Z][A-Z\s]{1,30})", text)
    given_match = re.search(r"Given Name\(?s?\)?\s*\n?[^A-Za-z\n]{0,4}([A-Z][A-Z\s]{1,30})", text)
    surname = surname_match.group(1).strip() if surname_match else ""

    if not surname and given_match:
        # The "Surname" label itself sometimes gets OCR-garbled past recognition -
        # the value still reliably appears as an all-caps line shortly before the
        # (more reliably OCR'd) "Given Name(s)" label.
        lines_before = [l.strip() for l in text[: given_match.start()].split("\n") if l.strip()]
        for candidate in reversed(lines_before[-4:]):
            if re.fullmatch(r"[A-Z][A-Z\s]{1,30}", candidate):
                surname = candidate
                break

    given = given_match.group(1).strip() if given_match else ""
    if surname or given:
        result["Name"] = f"{given} {surname}".strip()

    dob_match = re.search(r"Date of Birth[^\d]{0,15}(\d{2}[/-]\d{2}[/-]\d{4})", text, re.IGNORECASE)
    if dob_match:
        result["DOB"] = dob_match.group(1)

    sex_match = re.search(r"Sex\s*\n?[^A-Za-z\n]{0,4}([MF])\b", text)
    if not sex_match and dob_match:
        # "Date of Birth" and "Sex" are adjacent columns that sometimes collapse
        # onto one OCR'd line - the Sex value can end up right after the DOB
        # value instead of after its own label.
        after_dob = text[dob_match.end():dob_match.end() + 10]
        sex_match = re.search(r"\b([MF])\b", after_dob)
    if sex_match:
        result["Gender"] = "Male" if sex_match.group(1) == "M" else "Female"

    passport_match = re.search(r"Passport No\.?\s*\n?[^A-Za-z\n]{0,4}([A-Z]\d{7})", text)
    if passport_match:
        result["ID Number"] = passport_match.group(1)
    else:
        mrz_match = re.search(r"\n([A-Z]\d{7})<\d", text)
        if mrz_match:
            result["ID Number"] = mrz_match.group(1)
    return result


if uploaded_files:
    cache_key = f"id_extractor_rows_{id_type}"  # dict keyed by (name, size) -> row dict
    raw_text_key = f"id_extractor_raw_texts_{id_type}"

    if cache_key not in st.session_state:
        st.session_state[cache_key] = {}
    if raw_text_key not in st.session_state:
        st.session_state[raw_text_key] = {}

    current_identities = [(f.name, f.size) for f in uploaded_files]
    new_files = [f for f in uploaded_files if (f.name, f.size) not in st.session_state[cache_key]]

    if new_files:
        # Only OCR files that haven't been processed yet for this ID type - a file
        # already in the results (possibly hand-edited by now) is left completely
        # untouched, even though the uploader widget re-returns the full current
        # file list on every rerun. Re-keying off the whole batch as one unit
        # would silently wipe out any edit the moment a new file gets added.
        progress = st.progress(0.0, text="Reading documents...")
        for i, f in enumerate(new_files):
            text, angle, confidence = ocr_uploaded_file(f)
            fields = extract_aadhaar_fields(text) if id_type == "Aadhaar" else extract_passport_fields(text)
            missing = [k for k, v in fields.items() if not v]
            flag = f"⚠️ Missing: {', '.join(missing)}" if missing else ""
            st.session_state[cache_key][(f.name, f.size)] = {
                "File Name": f.name, "Flag": flag,
                "Name": fields["Name"], "Gender": fields["Gender"], "DOB": fields["DOB"],
                "ID Number": fields["ID Number"],
            }
            st.session_state[raw_text_key][f.name] = text
            progress.progress((i + 1) / len(new_files), text=f"Read {i + 1}/{len(new_files)}")
        progress.empty()

    # Only show rows for files currently in the uploader - if a file gets removed
    # from the widget, its row drops out of view too (though it stays cached, so
    # re-adding the same file later won't trigger a needless re-OCR).
    results_df = pd.DataFrame([st.session_state[cache_key][ident] for ident in current_identities])

    flagged_count = (results_df["Flag"] != "").sum()
    if flagged_count:
        st.warning(f"{flagged_count} of {len(results_df)} row(s) have a field that couldn't be confidently read - check the Flag column and correct directly in the table.")

    st.subheader("Review & Correct")
    edited_df = st.data_editor(
        results_df,
        use_container_width=True,
        hide_index=True,
        disabled=["File Name", "Flag"],  # identifiers, not meant to be hand-edited
        column_config={
            "Gender": st.column_config.SelectboxColumn("Gender", options=["", "Male", "Female"]),
            "ID Number": st.column_config.TextColumn("Aadhaar Number" if id_type == "Aadhaar" else "Passport Number"),
        },
        key=f"id_extractor_editor_{id_type}",
    )
    # Write edits back into the per-file cache immediately, so they survive the
    # next rerun even if more files get added afterward.
    for ident, (_, row) in zip(current_identities, edited_df.iterrows()):
        st.session_state[cache_key][ident] = row.to_dict()

    with st.expander("Raw OCR text (for reference if something looks wrong)"):
        for fname, text in st.session_state[raw_text_key].items():
            st.caption(fname)
            st.text(text)

    output_df = edited_df.drop(columns=["Flag"]).copy()
    output_df.insert(1, "ID Type", id_type)
    csv_bytes = output_df.to_csv(index=False).encode("utf-8")
    st.download_button("Download as CSV", csv_bytes, "id_data.csv", "text/csv")