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


FLIGHT_NO_PATTERN = re.compile(r"\b([A-Z0-9]{2,4}\s\d{3,5})\b")
# Allows both "XXX-YYY" and "XXX - YYY" (some exports space the dash out).
SECTOR_PATTERN = re.compile(r"\b([A-Z]{3})\s*-\s*([A-Z]{3})\b")


def chain_sector(legs):
    """legs: list of (flight_no, origin, dest). Joins them into e.g. 'IXE-BOM-DEL'."""
    if not legs:
        return "N/A"
    codes = [origin for _, origin, dest in legs]
    codes.append(legs[-1][2])
    return "-".join(codes)


def chain_flights(legs):
    return ", ".join(fn for fn, _, _ in legs) if legs else "N/A"


def split_onward_return(legs, trip_type="auto"):
    """
    Given an ordered list of (flight_no, origin, dest) legs, splits them into an onward
    chain and a return chain.

    trip_type:
    - "auto" (default): infer the split using the chain-closing heuristic below.
    - "one_way" / "connecting": never split - every leg is chained into the onward
      itinerary. Use this when the person already knows there's no return leg; it's a
      hard guarantee against stray text being misread as a phantom return (which is
      exactly the class of bug the auto heuristic can fall into on noisy PDFs).
    - "round_trip": still uses the same heuristic to find the split point (we don't know
      *where* it is without one), but callers can treat an empty return_legs result as a
      signal to warn the person, since they've told us a return leg should exist.

    The auto heuristic: a leg continues the onward chain if its origin matches the
    previous leg's destination AND it doesn't close the loop back to the very first
    origin (that's what distinguishes a connecting/multi-leg one-way itinerary, e.g.
    IXE-BOM then BOM-DEL, from a round trip, e.g. BOM-JAI then JAI-BOM).
    """
    if not legs:
        return [], []
    if trip_type in ("one_way", "connecting"):
        return legs, []
    onward = [legs[0]]
    i = 1
    while i < len(legs) and legs[i][1] == onward[-1][2] and legs[i][2] != onward[0][1]:
        onward.append(legs[i])
        i += 1
    return onward, legs[i:]


# ---------- Format A: multi-passenger agency ticket (Akbar Travels style) ----------

def _leg_direction(text, pos):
    """Return 'ONWARD' or 'RETURN' depending on which label most recently precedes pos."""
    onward_positions = [m.start() for m in re.finditer(r"\bONWARD\b", text)]
    return_positions = [m.start() for m in re.finditer(r"\bRETURN\b", text)]
    last_onward = max([p for p in onward_positions if p <= pos], default=-1)
    last_return = max([p for p in return_positions if p <= pos], default=-1)
    return "RETURN" if last_return > last_onward else "ONWARD"


def extract_agency_format(text, trip_type="auto"):
    # PNR
    pnr_match = re.search(r"(?:CRS Ref|Airline Ref)\s*:\s*([A-Z0-9]{5,8})", text, re.IGNORECASE)
    pnr = pnr_match.group(1).upper() if pnr_match else "Not Found"

    # City name -> 3-letter code map. Multi-word city names (e.g. "Mopa North Goa") are
    # captured in full, not just the last word, since the header line match below needs
    # to recognize the whole name to correctly split a route like "Mumbai Mopa North Goa".
    # An optional "(Alias)" segment - e.g. "Pune (Poona) [PNQ]" - is skipped over rather
    # than included, so the map key stays the plain city name that also appears in the
    # route header line ("Bangalore Pune (Poona)" contains "Pune", not "Pune (Poona)").
    # The negative lookahead blocks common airport-description words (International,
    # Airport, Terminal, Domestic) from extending the chain - without it, pdfplumber
    # sometimes linearizes two adjacent table cells (e.g. "Bengaluru International
    # Airport" description immediately followed by the *next* leg's real city name) with
    # only a newline between them, and the chain would otherwise bridge across both.
    city_to_code = {}
    _block = r"(?:International|Airport|Terminal|Domestic)\b"
    for m in re.finditer(
        r"(?<![A-Za-z])(?!" + _block + r")([A-Z][a-zA-Z]+(?:\s(?!" + _block + r")[A-Z][a-zA-Z]+)*)"
        r"(?:\s*\([A-Za-z]+\))?\s*\[([A-Z]{3})\]",
        text,
    ):
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

    onward_sector, onward_flight_no = chain_sector(onward_legs), chain_flights(onward_legs)
    return_sector = chain_sector(return_legs) if return_legs else "N/A"
    return_flight_no = chain_flights(return_legs) if return_legs else "N/A"

    warning = None
    if trip_type == "round_trip" and not return_legs:
        warning = "You marked this as a round trip, but no RETURN section was found in the ticket."
    elif trip_type in ("one_way", "connecting") and return_legs:
        warning = "You marked this as one-way, but the ticket has a RETURN section - check it wasn't missed."

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
    return passengers, warning


# ---------- Format B: single-passenger-per-page IndiGo boarding pass / itinerary ----------

def extract_boarding_pass_format(text, ocr_name_lookup=None, trip_type="auto"):
    """
    ocr_name_lookup: optional dict {page_index: "Mr Jaitra Talreja"} supplied by the caller
    when the passenger name cannot be found in the normal text layer (see note below).
    """
    results = []
    pnr_match = re.search(r"\b([A-Z0-9]{6})\s+Confirmed\b", text)
    pnr = pnr_match.group(1).upper() if pnr_match else "Not Found"

    name_pattern = re.compile(r"(Mr|Ms|Mrs|Mstr)\s+([A-Za-z][A-Za-z\s]{2,39}?)\s+Adult", re.IGNORECASE)
    gender_pattern = re.compile(r"Adult\s*\|\s*(Male|Female)\s*\|", re.IGNORECASE)

    # Split into per-passenger blocks using the "Passenger Information" heading as the anchor,
    # which is present once per passenger across every known sub-format - unlike page-number
    # footers, which vary ("1 of 24" vs "1/8") and aren't a safe thing to split on.
    block_starts = [m.start() for m in re.finditer(r"Passenger Information", text)]
    block_bounds = block_starts[1:] + [len(text)]

    # Some exports (e.g. GoIndigo's own itinerary PDF) follow the flight legs with a
    # "*Booking date reflects..." footnote and then, on later pages, baggage/fare/loyalty
    # summary tables full of stray numbers - without a boundary there, those can get
    # misread as a phantom extra leg. This note reliably marks the end of real leg data
    # in every sub-format seen so far.
    footnote_pattern = re.compile(r"Booking date reflects", re.IGNORECASE)

    warning = None
    for idx, (start, end) in enumerate(zip(block_starts, block_bounds)):
        raw_chunk = text[start:end]
        footnote_match = footnote_pattern.search(raw_chunk)
        chunk = raw_chunk[:footnote_match.start()] if footnote_match else raw_chunk

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

        # Every sector pair anywhere in the block, and every flight number anywhere in the
        # block, in document order - then split_onward_return works out which are a single
        # connecting itinerary (chained into one Sector/Flight Number) vs a genuine round
        # trip (split into Sector/Flight Number + Return Sector/Return Flight Number).
        sector_pairs = SECTOR_PATTERN.findall(chunk)
        flight_nos = [re.sub(r"\s+", " ", fn).strip() for fn in FLIGHT_NO_PATTERN.findall(chunk)]
        legs = [
            (flight_nos[i] if i < len(flight_nos) else "Not Found", origin, dest)
            for i, (origin, dest) in enumerate(sector_pairs)
        ]
        onward_legs, return_legs = split_onward_return(legs, trip_type=trip_type)

        onward_sector = chain_sector(onward_legs) if onward_legs else "Not Found"
        onward_flight_no = chain_flights(onward_legs) if onward_legs else "Not Found"
        return_sector = chain_sector(return_legs) if return_legs else "N/A"
        return_flight_no = chain_flights(return_legs) if return_legs else "N/A"

        if trip_type == "round_trip" and not return_legs:
            warning = "You marked this as a round trip, but no return leg was found for at least one passenger."

        gender = explicit_gender.group(1).capitalize() if explicit_gender else _gender_from_title(title)

        results.append({
            "PNR": pnr, "Name": name, "Gender": gender,
            "Sector": onward_sector, "Flight Number": onward_flight_no,
            "Return Sector": return_sector, "Return Flight Number": return_flight_no,
        })
    return results, warning


# ---------- Format C: Air India Express itinerary ----------
# NOTE: built and tested against a single real sample (a one-way, single-passenger
# itinerary). The Onward/Return chaining and multi-passenger handling follow the same
# logic as Format B and are expected to generalize, but haven't been verified against a
# real round-trip or multi-passenger Air India Express PDF yet - flag any mismatch you
# see on those so the pattern can be corrected against real data rather than guessed at.

def extract_air_india_express_format(text, trip_type="auto"):
    pnr_match = re.search(r"\bPNR\s*:?\s*([A-Z0-9]{6})\b", text)
    pnr = pnr_match.group(1).upper() if pnr_match else "Not Found"

    # Flight header lines look like "IX 1275 Mon, Jan 12 2026 Onward" or "... Return".
    leg_header_pattern = re.compile(
        r"([A-Z0-9]{2,4}\s\d{3,5})[^\n]*?\b(Onward|Return)\b", re.IGNORECASE
    )
    leg_headers = list(leg_header_pattern.finditer(text))

    onward_legs, return_legs = [], []
    for i, hm in enumerate(leg_headers):
        flight_no = re.sub(r"\s+", " ", hm.group(1)).strip()
        direction = hm.group(2).upper()
        block_end = leg_headers[i + 1].start() if i + 1 < len(leg_headers) else len(text)
        block_text = text[hm.end():block_end]
        sm = SECTOR_PATTERN.search(block_text)
        origin, dest = (sm.group(1), sm.group(2)) if sm else ("???", "???")
        leg = (flight_no, origin, dest)
        (return_legs if direction == "RETURN" else onward_legs).append(leg)

    onward_sector = chain_sector(onward_legs) if onward_legs else "Not Found"
    onward_flight_no = chain_flights(onward_legs) if onward_legs else "Not Found"
    return_sector = chain_sector(return_legs) if return_legs else "N/A"
    return_flight_no = chain_flights(return_legs) if return_legs else "N/A"

    warning = None
    if trip_type == "round_trip" and not return_legs:
        warning = "You marked this as a round trip, but no 'Return' flight was found in the ticket."
    elif trip_type in ("one_way", "connecting") and return_legs:
        warning = "You marked this as one-way, but the ticket has a 'Return' flight - check it wasn't missed."

    # Single passenger per document in the sample seen so far: "Name Seat Add Ons\nMr X Y"
    # with no trailing "Adult" marker - the name instead runs up against baggage/fare text,
    # so it's bounded by the next digit or newline instead.
    passengers = []
    name_pattern = re.compile(r"(Mr|Ms|Mrs|Mstr)\s+([A-Za-z][A-Za-z\s]{2,39}?)(?=\s+\d|\n)")
    seen = set()
    for m in name_pattern.finditer(text):
        title, raw_name = m.group(1), m.group(2)
        name = re.sub(r"\s+", " ", raw_name).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        passengers.append({
            "PNR": pnr, "Name": name, "Gender": _gender_from_title(title),
            "Sector": onward_sector, "Flight Number": onward_flight_no,
            "Return Sector": return_sector, "Return Flight Number": return_flight_no,
        })
    return passengers, warning


def extract_ticket_data(text, ocr_name_lookup=None, trip_type="auto"):
    """
    trip_type: "auto" (default), "one_way", "connecting", or "round_trip" - an optional
    hint from the person uploading the ticket, when they already know the itinerary
    shape. "one_way"/"connecting" guarantee no return leg is ever fabricated from stray
    text; "round_trip" keeps the normal split logic but the returned warning flags it if
    no return leg was actually found, so a mismatch surfaces instead of failing silently.
    Returns (rows, warning_or_None).
    """
    if "Traveler(s) Information" in text:
        return extract_agency_format(text, trip_type=trip_type)
    if "Air India Express" in text or re.search(r"\bPNR\s*:", text):
        return extract_air_india_express_format(text, trip_type=trip_type)
    if "Departing Flight" in text or "PNR/Booking Ref" in text:
        return extract_boarding_pass_format(text, ocr_name_lookup=ocr_name_lookup, trip_type=trip_type)
    return [], None