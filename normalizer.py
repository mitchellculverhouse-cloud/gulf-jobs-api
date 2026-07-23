ALLOWED_COUNTRIES = [
    "Saudi Arabia",
    "United Arab Emirates",
    "Qatar",
    "Kuwait",
    "Bahrain",
    "Oman"
]


COUNTRY_ALIASES = {
    "ksa": "Saudi Arabia",
    "saudi": "Saudi Arabia",
    "saudi arabia": "Saudi Arabia",

    "uae": "United Arab Emirates",
    "u.a.e": "United Arab Emirates",
    "united arab emirates": "United Arab Emirates",

    "qatar": "Qatar",

    "kuwait": "Kuwait",

    "bahrain": "Bahrain",

    "oman": "Oman"
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
