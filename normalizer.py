ALLOWED_COUNTRIES = [
    "Saudi Arabia",
    "United Arab Emirates",
    "Qatar",
    "Kuwait",
    "Bahrain",
    "Oman"
]

import re
from datetime import date, datetime
from urllib.parse import urlsplit, urlunsplit


COUNTRY_ALIASES = {
    "sa": "Saudi Arabia",
    "ksa": "Saudi Arabia",
    "saudi": "Saudi Arabia",
    "saudi arabia": "Saudi Arabia",

    "uae": "United Arab Emirates",
    "u.a.e": "United Arab Emirates",
    "united arab emirates": "United Arab Emirates",
    "ae": "United Arab Emirates",

    "qatar": "Qatar",
    "qa": "Qatar",

    "kuwait": "Kuwait",
    "kw": "Kuwait",

    "bahrain": "Bahrain",
    "bh": "Bahrain",

    "oman": "Oman",
    "om": "Oman"
}


def normalize_country(country):

    if not country:
        return None

    country = country.strip().lower()

    if country in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[country]

    for key, value in COUNTRY_ALIASES.items():
        if key in country:
            return value

    return None



def allowed_country(country):

    normalized = normalize_country(country)

    if normalized in ALLOWED_COUNTRIES:
        return True

    return False


def clean_text(value):
    """Collapse whitespace without manufacturing a value for missing data."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def canonical_url(value, base_url=None):
    from urllib.parse import urljoin

    value = clean_text(value)
    if not value:
        return ""
    absolute = urljoin(base_url or "", value)
    parts = urlsplit(absolute)
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    netloc = hostname
    if parts.port and not ((scheme == "http" and parts.port == 80) or (scheme == "https" and parts.port == 443)):
        netloc += f":{parts.port}"
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, "", ""))


def normalize_date(value):
    value = clean_text(value)
    if not value:
        return None
    candidate = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate).date().isoformat()
    except ValueError:
        pass
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_city(value):
    return clean_text(value)


def normalize_job_type(value):
    value = clean_text(value)
    comparable = value.lower().replace("_", " ")
    aliases = {
        "fulltime": "Full Time", "full time": "Full Time", "full-time": "Full Time",
        "parttime": "Part Time", "part time": "Part Time", "part-time": "Part Time",
        "contractor": "Contract", "contract": "Contract", "temporary": "Temporary",
        "intern": "Internship", "internship": "Internship",
        "freelance / project": "Freelance / Project",
        "freelance/project": "Freelance / Project", "freelance": "Freelance / Project",
    }
    return aliases.get(comparable, value)


def normalize_work_mode(value):
    value = clean_text(value)
    aliases = {"remote": "Remote", "work from home": "Remote", "hybrid": "Hybrid",
               "on-site": "On-site", "onsite": "On-site", "on site": "On-site"}
    return aliases.get(value.lower(), value)


def normalize_currency(value):
    value = clean_text(value).upper()
    aliases = {"SR": "SAR", "SAR": "SAR", "AED": "AED", "QAR": "QAR",
               "KWD": "KWD", "BHD": "BHD", "OMR": "OMR", "$": "USD"}
    return aliases.get(value, value)


def normalize_salary_period(value):
    value = clean_text(value).lower().replace("per ", "")
    aliases = {"hour": "Hourly", "day": "Daily", "week": "Weekly",
               "month": "Monthly", "year": "Yearly", "annual": "Yearly", "annually": "Yearly"}
    return aliases.get(value, clean_text(value))
