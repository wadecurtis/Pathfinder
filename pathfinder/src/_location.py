"""Shared location parsing for job search configuration."""


def parse_location(entry: str) -> tuple[str, str]:
    """Parse a location entry into (country, location_string).

    Format: "country[, state[, city]]"
    - "canada"                   -> ("canada", "")
    - "usa, california"          -> ("usa", "California")
    - "canada, alberta, calgary" -> ("canada", "Calgary, Alberta")
    """
    parts = [p.strip() for p in entry.split(",")]
    country = parts[0].lower()
    # Build location string from remaining parts in reverse (city, state)
    location = ", ".join(parts[1:][::-1]).strip() if len(parts) > 1 else ""
    return country, location


def parse_locations(raw: str | list[str]) -> list[tuple[str, str]]:
    """Parse a locations config value (string or list) into [(country, location), ...]."""
    if isinstance(raw, str):
        raw = [raw]
    return [parse_location(entry) for entry in raw]
