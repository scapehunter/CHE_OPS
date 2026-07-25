import re


def _gender_from_title(title):
    t = title.lower()
    if t.startswith("mstr"):
        return "Male (Child)"
    if t.startswith("mr"):
        return "Male (Adult)"
    if t in ("ms", "mrs"):
        return "Female"
    return "Unknown"


# ---------- Format A: multi-passenger agency ticket (Akbar Travels style) ----------

def _leg_direction(text, pos):
    """Return 'ONWARD' or 'RETURN' depending on which label most recently precedes pos."""
    onward_positions = [m.start() for m in re.finditer(r"\bONWARD\b", text)]
    return_positions = [m.start() for m in re.finditer(r"\bRETURN\b", text)]
    last_onward = max([p for p in onward_positions if p <= pos], default=-1)
    last_return = max([p for p in return_positions if p <= pos], default=-1)
    return "RETURN" if last_return > last_onward else "ONWARD"


FLIGHT_NO_PATTERN = re.compile(r"\b([A-Z0-9]{2,4}\s\d{3,5})\b")


def extract_agency_format(text):
    # PNR
    pnr_match = re.search(r"(?:CRS Ref|Airline Ref)\s*:\s*([A-Z0-9]{5,8})", text, re.IGNORECASE)
    pnr = pnr_match.group(1).upper() if pnr_match else "Not Found"

    # City name -> 3-letter code map. Multi-word city names (e.g. "Mopa North Goa") are
    # captured in full, not just the last word, since the header line match below needs
    # to recognize the whole name to correctly split a route like "Mumbai Mopa North Goa".
    city_to_code = {}
    for m in re.finditer(r"([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)*)\s*\[([A-Z]{3})\]", text):
        city_to_code[m.group(1)] = m.group(2)
    # Try longest city names first so "Mopa North Goa" matches before a shorter overlapping name would.
    known_cities = sorted(city_to_code.keys(), key=len, reverse=True)

    traveler_start = text.find("Traveler(s) Information")
    search_end = traveler_start if traveler_start != -1 else len(text)

    # Each leg's route line sits on its own line, directly above "Airline Ref :" - e.g.
    # "ONWARD Mumbai Mopa North Goa" or, for a connecting leg with no explicit label,
    # just "Mumbai Mangalore". Capturing the whole line (rather than assuming exactly two
    # single-word city tokens) lets us handle multi-word city names correctly.
    header_pattern = re.compile(r"([^\n]+)\n(?:Airline Ref)\s*:\s*([A-Z0-9]{5,8})")
    headers = [m for m in header_pattern.finditer(text, 0, search_end)]

    onward_legs, return_legs = [], []

    for i, hm in enumerate(headers):
        route_line = hm.group(1)

        # Find which known cities appear in this route line, in the order they appear
        # (skipping any that overlap a longer city name already matched).
        found = []
        occupied = []
        for city in known_cities:
            idx = route_line.find(city)
            if idx == -1:
                continue
            span = (idx, idx + len(city))
            if any(not (span[1] <= s or span[0] >= e) for s, e in occupied):
                continue  # overlaps a longer city name already matched
            occupied.append(span)
            found.append((idx, city))
        found.sort(key=lambda t: t[0])

        origin_code = city_to_code.get(found[0][1], "???") if len(found) >= 1 else "???"
        dest_code = city_to_code.get(found[1][1], "???") if len(found) >= 2 else "???"

        block_end = headers[i + 1].start() if i + 1 < len(headers) else search_end
        block_text = text[hm.end():block_end]
        fm = FLIGHT_NO_PATTERN.search(block_text)
        flight_no = re.sub(r"\s+", " ", fm.group(1)).strip() if fm else "Not Found"

        leg = (flight_no, origin_code, dest_code)
        if _leg_direction(text, hm.start()) == "RETURN":
            return_legs.append(leg)
        else:
            onward_legs.append(leg)

    def chain_sector(legs):
        if not legs:
            return "N/A"
        codes = [origin for _, origin, dest in legs]
        codes.append(legs[-1][2])
        return "-".join(codes)

    def chain_flights(legs):
        return ", ".join(fn for fn, _, _ in legs) if legs else "N/A"

    onward_sector, onward_flight_no = chain_sector(onward_legs), chain_flights(onward_legs)
    return_sector = chain_sector(return_legs) if return_legs else "N/A"
    return_flight_no = chain_flights(return_legs) if return_legs else "N/A"

    # Passengers: title + ALL-CAPS multi-word name (word tokens of 2+ letters, to avoid
    # swallowing the trailing "Nil Nil Nil Nil" columns that follow each row)
    passengers = []
    passenger_pattern = re.compile(r"(Mr|Ms|Mrs|Mstr)\.\s+((?:[A-Z]{2,}\s*)+)")
    seen = set()
    for m in passenger_pattern.finditer(text):
        title, raw_name = m.group(1), m.group(2)
        name = re.sub(r"\s+", " ", raw_name).strip()
        if name == "SWADESHI TRAVELS" or name in seen or not name:
            continue
        seen.add(name)
        passengers.append({
            "PNR": pnr, "Name": name, "Gender": _gender_from_title(title),
            "Sector": onward_sector, "Flight Number": onward_flight_no,
            "Return Sector": return_sector, "Return Flight Number": return_flight_no,
        })
    return passengers


# ---------- Format B: single-passenger-per-page IndiGo boarding pass ----------

def extract_boarding_pass_format(text, ocr_name_lookup=None):
    """
    ocr_name_lookup: optional dict {page_index: "Mr Jaitra Talreja"} supplied by the caller
    when the passenger name cannot be found in the normal text layer (see note below).
    """
    results = []
    pnr_match = re.search(r"\b([A-Z0-9]{6})\s+Confirmed\b", text)
    pnr = pnr_match.group(1).upper() if pnr_match else "Not Found"

    sector_pattern = re.compile(r"Sector\s+Seat\s+6E Add-ons\s*\n?\s*([A-Z]{3})\s*-\s*([A-Z]{3})")
    # Shared general pattern - doesn't require an aircraft-type suffix like "(A321)"/"(AIRBUS...)",
    # since not every airline/ticket export includes one.
    flight_pattern = FLIGHT_NO_PATTERN
    name_pattern = re.compile(r"(Mr|Ms|Mrs|Mstr)\s+([A-Za-z][A-Za-z\s]{2,39}?)\s+Adult", re.IGNORECASE)
    gender_pattern = re.compile(r"Adult\s*\|\s*(Male|Female)\s*\|", re.IGNORECASE)

    # Split into per-passenger blocks using the "Passenger Information" heading as the anchor,
    # which is present once per passenger across every known sub-format - unlike page-number
    # footers, which vary ("1 of 24" vs "1/8") and aren't a safe thing to split on.
    block_starts = [m.start() for m in re.finditer(r"Passenger Information", text)]
    block_bounds = block_starts[1:] + [len(text)]

    for idx, (start, end) in enumerate(zip(block_starts, block_bounds)):
        chunk = text[start:end]

        name_match = name_pattern.search(chunk)
        explicit_gender = gender_pattern.search(chunk)
        if name_match:
            title, raw_name = name_match.group(1), name_match.group(2)
            name = re.sub(r"\s+", " ", raw_name).strip()
        elif ocr_name_lookup and idx in ocr_name_lookup:
            # ocr_name_lookup[idx] is the full OCR'd text of that page. The layout is always
            # "<Title> <Name words...> <age qualifier: Adult/Child/Infant>\n\nSector ...", so we
            # take everything between the title and the "Sector" keyword (OCR reads plain English
            # words like "Sector" reliably even when it mangles the age qualifier) and drop the
            # last token, which is always that age qualifier - robust to OCR noise on that one
            # word without depending on it being capitalized correctly.
            ocr_page_text = ocr_name_lookup[idx]
            om = re.search(r"(Mr|Ms|Mrs|Mstr)\s+(.*?)\n\s*\n?\s*Sector", ocr_page_text, re.DOTALL)
            if om:
                title = om.group(1)
                words = om.group(2).split()
                name = " ".join(words[:-1]) if len(words) > 1 else " ".join(words)
            else:
                title, name = "Unknown", "Not Found"
        else:
            title, name = "Unknown", "Not Found (name missing from PDF text layer)"

        sector_matches = sector_pattern.findall(chunk)
        onward_sector = f"{sector_matches[0][0]}-{sector_matches[0][1]}" if sector_matches else "Not Found"
        return_sector = f"{sector_matches[1][0]}-{sector_matches[1][1]}" if len(sector_matches) > 1 else "N/A"

        flight_matches = flight_pattern.findall(chunk)
        onward_flight_no = re.sub(r"\s+", " ", flight_matches[0]).strip() if flight_matches else "Not Found"
        # Gate the return flight number on there actually being a second Sector entry -
        # a real round trip always has both together, so this avoids the (deliberately
        # loose) flight-number pattern picking up noise on genuinely one-way tickets
        # with a corrupted/partial text layer.
        return_flight_no = (
            re.sub(r"\s+", " ", flight_matches[1]).strip()
            if len(sector_matches) > 1 and len(flight_matches) > 1
            else "N/A"
        )

        gender = explicit_gender.group(1).capitalize() if explicit_gender else _gender_from_title(title)

        results.append({
            "PNR": pnr, "Name": name, "Gender": gender,
            "Sector": onward_sector, "Flight Number": onward_flight_no,
            "Return Sector": return_sector, "Return Flight Number": return_flight_no,
        })
    return results


def extract_ticket_data(text, ocr_name_lookup=None):
    if "Traveler(s) Information" in text:
        return extract_agency_format(text)
    if "Departing Flight" in text or "PNR/Booking Ref" in text:
        return extract_boarding_pass_format(text, ocr_name_lookup=ocr_name_lookup)
    return []