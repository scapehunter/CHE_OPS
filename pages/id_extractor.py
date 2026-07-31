import re

import pdfplumber
import pytesseract
import streamlit as st
from PIL import Image

st.title("🪪 ID Extractor")
st.write(
    "Upload an Aadhaar card or Passport (PDF, JPG, or PNG) to extract Name, Gender, "
    "Date of Birth, and ID Number."
)
st.info(
    "OCR on photographed ID documents isn't perfectly reliable - lighting, angle, and "
    "print quality all affect it. Every extracted field below is editable, so review "
    "against the actual document before using the result, rather than trusting it "
    "blindly.",
    icon="ℹ️",
)

id_type = st.radio("ID Type", ["Aadhaar", "Passport"], horizontal=True)

uploaded_file = st.file_uploader("Upload ID document", type=["pdf", "jpg", "jpeg", "png"], accept_multiple_files=False)


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


if uploaded_file:
    with st.spinner("Reading document..."):
        text, angle, confidence = ocr_uploaded_file(uploaded_file)

    fields = extract_aadhaar_fields(text) if id_type == "Aadhaar" else extract_passport_fields(text)

    missing = [k for k, v in fields.items() if not v]
    if missing:
        st.warning(f"Couldn't confidently read: {', '.join(missing)}. Fill these in manually below.")

    st.subheader("Review & Correct")
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("Name", value=fields["Name"])
        dob = st.text_input("Date of Birth (DD/MM/YYYY)", value=fields["DOB"])
    with col2:
        gender = st.selectbox(
            "Gender", ["", "Male", "Female"],
            index=["", "Male", "Female"].index(fields["Gender"]) if fields["Gender"] in ("", "Male", "Female") else 0,
        )
        id_number = st.text_input(
            "Aadhaar Number" if id_type == "Aadhaar" else "Passport Number",
            value=fields["ID Number"],
        )

    with st.expander("Raw OCR text (for reference if something looks wrong)"):
        st.text(text)

    if name and gender and dob and id_number:
        import pandas as pd
        result_df = pd.DataFrame([{
            "File Name": uploaded_file.name, "ID Type": id_type,
            "Name": name, "Gender": gender, "DOB": dob, "ID Number": id_number,
        }])
        st.dataframe(result_df, use_container_width=True, hide_index=True)
        csv_bytes = result_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download as CSV", csv_bytes, "id_data.csv", "text/csv")

