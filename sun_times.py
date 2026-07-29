"""
Sunrise/sunset calculation - fully self-contained and offline (no network calls,
no API keys, no external service dependency). Uses the astral library to compute
pure astronomical times from latitude/longitude/date.

Deliberately kept as its own module, separate from itinerary_logic.py, and only
ever imported where actually needed (not from the core generation/cascade code
at all) - a missing astral install, a computation failure, or missing coordinate
data should never affect the rest of the app. Every function here degrades
gracefully (returns None rather than raising) so a caller can simply skip
showing sun times if anything goes wrong, instead of crashing.
"""

try:
    from astral import LocationInfo
    from astral.sun import sun as _astral_sun
    ASTRAL_AVAILABLE = True
except ImportError:
    ASTRAL_AVAILABLE = False


DEFAULT_TIMEZONE = "Asia/Kolkata"
# All of this app's programs are India-based (per curious_hathi_programs.csv) - a
# single default timezone is a reasonable assumption for now, not something stated
# explicitly. If a program ever needs a different timezone, this is the one place
# to add per-program overrides later.


def get_sunrise_sunset(lat, lon, day_date, timezone=DEFAULT_TIMEZONE):
    """
    Returns (sunrise_str, sunset_str) as "HH:MM" in the given timezone, or
    (None, None) if astral isn't available, coordinates are missing/invalid, or
    the calculation fails for any reason (e.g. polar day/night at extreme
    latitudes - not a real concern for this app's India-based programs, but
    handled safely regardless).
    """
    if not ASTRAL_AVAILABLE or lat is None or lon is None:
        return None, None
    try:
        location = LocationInfo(latitude=lat, longitude=lon, timezone=timezone)
        result = _astral_sun(location.observer, date=day_date, tzinfo=location.timezone)
        return result["sunrise"].strftime("%H:%M"), result["sunset"].strftime("%H:%M")
    except Exception:
        return None, None
