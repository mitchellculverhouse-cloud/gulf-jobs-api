import json
import re
import time
from collections import Counter

import requests
from bs4 import BeautifulSoup
from sqlalchemy.exc import IntegrityError
from urllib.parse import unquote, urlsplit

from database import Session
from models import Job
from normalizer import (
    canonical_url, clean_text, normalize_city, normalize_country, normalize_currency,
    normalize_date, normalize_job_type, normalize_salary_period,
    normalize_work_mode,
)
from sources import SOURCES


HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
JOB_FIELDS = (
    "title", "company_name", "country", "city", "area", "description", "skills",
    "category", "industry", "salary_min", "salary_max", "salary_currency",
    "salary_period", "job_type", "work_mode", "experience_level",
    "nationality_required", "gender_required", "arabic_required",
    "languages_required", "date_posted", "closing_date", "apply_url", "source",
)
WUZZUF_HOSTS = {"wuzzuf.net", "www.wuzzuf.net"}
WUZZUF_FILTER_FIELDS = ("category", "industry", "job_type", "work_mode")
WUZZUF_CATEGORIES = {
    "Accounting/Finance", "Administration", "Analyst/Research", "Banking",
    "Business Development",
    "C-Level Executive/GM/Director", "Creative/Design/Art", "Customer Service/Support",
    "Education/Teaching", "Engineering - Construction/Civil/Architecture",
    "Engineering - Mechanical/Electrical", "Engineering - Oil & Gas/Energy",
    "Engineering - Other", "Engineering - Telecom/Technology",
    "Hospitality/Hotels/Food Services", "Human Resources", "IT/Software Development",
    "Installation/Maintenance/Repair", "Legal", "Logistics/Supply Chain",
    "Manufacturing/Production", "Marketing/PR/Advertising", "Medical/Healthcare",
    "Media/Journalism/Publishing", "Operations/Management", "Pharmaceutical",
    "Project/Program Management", "Purchasing/Procurement", "Quality", "R&D/Science",
    "Sales/Retail", "Sports and Leisure", "Strategy/Consulting", "Tourism/Travel",
    "Training/Instructor", "Writing/Editorial",
}
WUZZUF_JOB_TYPES = {
    "Full Time", "Part Time", "Contract", "Temporary", "Internship",
    "Freelance / Project",
}
WUZZUF_WORK_MODES = {"Remote", "Hybrid", "On-site"}
WUZZUF_BACKFILL_FAILURE_DIAGNOSTICS = (
    "http_403", "http_429", "http_5xx", "http_other",
    "timeout", "connection_error", "parser_or_unexpected", "database",
)


class UnsupportedParserError(ValueError):
    pass


def _request(session, url, source):
    retries = max(0, int(source.get("max_retries", 0)))
    delay = max(0, float(source.get("retry_delay", 1)))
    timeout = source.get("timeout", 45)
    for attempt in range(retries + 1):
        try:
            response = session.get(url, headers=HEADERS, timeout=timeout)
            if response.status_code == 200:
                return response
            if response.status_code not in (408, 429) and response.status_code < 500:
                response.raise_for_status()
        except (requests.Timeout, requests.ConnectionError):
            if attempt == retries:
                raise
        if attempt < retries:
            time.sleep(delay * (attempt + 1))
    response.raise_for_status()


def _is_wuzzuf_job_url(url, allowed_hosts):
    parts = urlsplit(url)
    return (parts.hostname or "").lower() in allowed_hosts and "/jobs/p/" in parts.path


def parse_wuzzuf_listing(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    store = _wuzzuf_store(soup)
    jobs = []
    seen = set()
    allowed_hosts = WUZZUF_HOSTS
    for anchor in soup.find_all("a", href=True):
        try:
            url = canonical_url(anchor["href"], base_url)
            if not _is_wuzzuf_job_url(url, allowed_hosts) or url in seen:
                continue
            title = clean_text(anchor.get_text(" ", strip=True))
            if not title:
                continue
            seen.add(url)
            listing_values = _wuzzuf_listing_classifications(store, url)
            card_values = _wuzzuf_card_classifications(anchor, url, base_url)
            for field, value in card_values.items():
                if field == "_authoritative_fields":
                    continue
                listing_values.setdefault(field, value)
            authoritative = tuple(
                field for field in ("category", "job_type", "work_mode")
                if listing_values.get(field)
            )
            if authoritative:
                listing_values["_authoritative_fields"] = authoritative
            jobs.append({"title": title, "link": url, **listing_values})
        except (KeyError, TypeError, ValueError):
            continue
    return jobs


def _wuzzuf_card_classifications(anchor, url, base_url):
    """Extract exact WUZZUF taxonomy labels from the anchor's own job card."""
    card = None
    for parent in anchor.parents:
        if parent.name not in {"article", "li", "div"}:
            continue
        job_urls = {
            canonical_url(item.get("href"), base_url)
            for item in parent.find_all("a", href=True)
            if _is_wuzzuf_job_url(canonical_url(item.get("href"), base_url), WUZZUF_HOSTS)
        }
        if job_urls == {url}:
            card = parent
            if parent.name in {"article", "li"} or parent.get("data-testid") == "job-card":
                break
        elif card is not None:
            break
    if card is None:
        return {}

    labels = {
        clean_text(item.get_text(" ", strip=True))
        for item in card.find_all(["a", "span", "li"])
        if item is not anchor
    }
    categories = [value for value in WUZZUF_CATEGORIES if value in labels]
    job_types = [value for value in WUZZUF_JOB_TYPES if value in labels]
    work_modes = [value for value in WUZZUF_WORK_MODES if value in labels]
    values = {
        "category": ", ".join(sorted(categories)),
        "job_type": ", ".join(sorted(job_types)),
        "work_mode": work_modes[0] if len(work_modes) == 1 else "",
    }
    authoritative = tuple(field for field, value in values.items() if value)
    if authoritative:
        values["_authoritative_fields"] = authoritative
    return {field: value for field, value in values.items() if value}


def _wuzzuf_listing_classifications(store, url):
    """Return only classifications carried by the matching listing entity.

    WUZZUF's server-rendered state associates work roles, work types, and the
    workplace arrangement with a job URI.  Matching that URI avoids treating
    unrelated card links (notably keyword links) as categories.
    """
    if not isinstance(store, dict):
        return {}
    jobs = store.get("entities", {}).get("job", {}).get("collection", {})
    path = _wuzzuf_entity_path(url)
    entity = next((item for item in jobs.values()
                   if _wuzzuf_entity_path(item.get("attributes", {}).get("uri")) == path), None)
    if not entity:
        return {}
    attributes = entity.get("attributes", {})
    values = {
        "category": ", ".join(_named_values(attributes.get("workRoles"))),
        "job_type": ", ".join(
            _named_values(attributes.get("workTypes"), normalize_job_type)
        ),
        "work_mode": normalize_work_mode(
            _named(attributes.get("workplaceArrangement") or {})
        ),
    }
    authoritative = tuple(field for field, value in values.items() if value)
    if authoritative:
        values["_authoritative_fields"] = authoritative
    return {field: value for field, value in values.items() if value}


def count_wuzzuf_listing_links(html, base_url):
    count = 0
    soup = BeautifulSoup(html, "html.parser")
    allowed_hosts = WUZZUF_HOSTS
    for anchor in soup.find_all("a", href=True):
        try:
            url = canonical_url(anchor["href"], base_url)
            if _is_wuzzuf_job_url(url, allowed_hosts) and clean_text(anchor.get_text(" ", strip=True)):
                count += 1
        except (KeyError, TypeError, ValueError):
            continue
    return count

def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _jobposting(data):
    for item in _walk(data):
        kind = item.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "JobPosting" in kinds:
            return item
    return None


def _json_scripts(soup, ld_only=False):
    selector = 'script[type="application/ld+json"]' if ld_only else "script"
    for script in soup.select(selector):
        text = script.string or script.get_text()
        if not text or not text.lstrip().startswith(("{", "[")):
            continue
        try:
            yield json.loads(text)
        except json.JSONDecodeError:
            continue


def _as_text(value):
    if isinstance(value, list):
        return ", ".join(filter(None, (_as_text(item) for item in value)))
    if isinstance(value, dict):
        return clean_text(value.get("name") or value.get("value") or "")
    return clean_text(value)


def _html_text(value):
    """Turn source HTML into readable text while retaining block boundaries."""
    soup = BeautifulSoup(str(value or ""), "html.parser")
    for item in soup.find_all("li"):
        item.insert_before("\n- ")
    for block in soup.find_all(["br", "p", "div", "ul", "ol"]):
        block.append("\n")
    lines = [clean_text(line) for line in soup.get_text().splitlines()]
    return "\n".join(line for line in lines if line)


def _location(posting):
    location = posting.get("jobLocation") or {}
    if isinstance(location, list):
        location = location[0] if location else {}
    address = location.get("address", {}) if isinstance(location, dict) else {}
    if isinstance(address, str):
        return "", "", clean_text(address)
    return (_as_text(address.get("addressCountry")), _as_text(address.get("addressLocality")),
            _as_text(address.get("streetAddress") or address.get("addressRegion")))


def _salary(posting):
    salary = posting.get("baseSalary") or {}
    if not isinstance(salary, dict):
        return "", "", "", ""
    currency = salary.get("currency", "")
    value = salary.get("value", salary)
    if not isinstance(value, dict):
        value = {"value": value}
    minimum = value.get("minValue", value.get("value", ""))
    maximum = value.get("maxValue", value.get("value", ""))
    return _as_text(minimum), _as_text(maximum), normalize_currency(currency), normalize_salary_period(value.get("unitText", ""))


def _from_posting(posting):
    country, city, area = _location(posting)
    salary_min, salary_max, currency, period = _salary(posting)
    organization = posting.get("hiringOrganization") or {}
    return {
        "title": _as_text(posting.get("title")),
        "company_name": _as_text(organization),
        "country": normalize_country(country), "city": normalize_city(city), "area": clean_text(area),
        "description": _html_text(posting.get("description")),
        "skills": _as_text(posting.get("skills") or posting.get("qualifications")),
        "category": _as_text(posting.get("occupationalCategory")),
        "industry": _as_text(posting.get("industry")),
        "salary_min": salary_min, "salary_max": salary_max,
        "salary_currency": currency, "salary_period": period,
        "job_type": normalize_job_type(_as_text(posting.get("employmentType"))),
        "work_mode": normalize_work_mode(_as_text(posting.get("jobLocationType"))),
        "experience_level": _as_text(posting.get("experienceRequirements")),
        "date_posted": normalize_date(posting.get("datePosted")),
        "closing_date": normalize_date(posting.get("validThrough")),
    }


LABELS = {
    "skills": ("skills",), "category": ("job category", "category"), "industry": ("industry",),
    "experience_level": ("experience needed", "experience level"),
    "nationality_required": ("nationality",), "gender_required": ("gender",),
    "arabic_required": ("arabic required", "arabic requirement"),
    "languages_required": ("languages", "language"), "job_type": ("job type",),
    "work_mode": ("work mode", "work location"), "closing_date": ("closing date",),
}


def _wuzzuf_store(soup):
    """Read WUZZUF's server-rendered Redux state without executing page scripts."""
    marker = "Wuzzuf.initialStoreState"
    for script in soup.find_all("script"):
        text = script.string or script.get_text()
        start = text.find(marker)
        if start < 0:
            continue
        start = text.find("{", start + len(marker))
        decoder = json.JSONDecoder()
        try:
            return decoder.raw_decode(text[start:])[0]
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _named(value):
    return _as_text(value.get("displayedName") or value.get("name")) if isinstance(value, dict) else _as_text(value)


def _named_values(values, normalizer=clean_text):
    """Preserve ordered structured labels while removing blank duplicates."""
    result = []
    seen = set()
    for item in values or []:
        value = normalizer(_named(item))
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _experience(years):
    if not isinstance(years, dict):
        return ""
    minimum, maximum = years.get("min"), years.get("max")
    if minimum is not None and maximum is not None:
        return f"{minimum} - {maximum} years"
    if minimum is not None:
        return f"{minimum}+ years"
    if maximum is not None:
        return f"Up to {maximum} years"
    return ""


def _wuzzuf_date(value):
    value = clean_text(value)
    match = re.match(r"^(\d{2})/(\d{2})/(\d{4})", value)
    return f"{match[3]}-{match[1]}-{match[2]}" if match else normalize_date(value)


def _wuzzuf_entity_path(value):
    """Normalize equivalent localized WUZZUF job paths for exact comparison."""
    path = unquote(urlsplit(clean_text(value)).path)
    parts = [part for part in path.split("/") if part]
    if parts and parts[0].casefold() in {"ar", "en"}:
        parts.pop(0)
    return "/".join(parts)


def _from_wuzzuf_store(store, url):
    if not isinstance(store, dict):
        return {}
    jobs = store.get("entities", {}).get("job", {}).get("collection", {})
    path = _wuzzuf_entity_path(url)
    entity = next((item for item in jobs.values()
                   if _wuzzuf_entity_path(item.get("attributes", {}).get("uri")) == path), None)
    if not entity:
        return {}
    job = entity.get("attributes", {})
    location = job.get("location") or {}
    salary = job.get("salary") or {}
    company_ref = entity.get("relationships", {}).get("company", {}).get("data") or {}
    companies = store.get("entities", {}).get("company", {}).get("collection", {})
    company = companies.get(str(company_ref.get("id")), {}).get("attributes", {})
    if job.get("hideCompany"):
        company_name = "Confidential Company"
    else:
        company_name = _as_text(company.get("name"))
    requirements = _html_text(job.get("requirements"))
    description = _html_text(job.get("description"))
    requirements = requirements if _meaningful_text(requirements) else ""
    description = description if _meaningful_text(description) else ""
    if requirements:
        description = f"{description}\n\nJob Requirements\n{requirements}" if description else requirements
    roles = ", ".join(_named_values(job.get("workRoles")))
    skills = ", ".join(_named_values(job.get("keywords")))
    work_types = ", ".join(_named_values(job.get("workTypes"), normalize_job_type))
    industry = ", ".join(_named_values(company.get("workIndustries")))
    result = {
        "title": _as_text(job.get("title")), "company_name": company_name,
        "country": normalize_country(_named(location.get("country") or {})),
        "city": normalize_city(_named(location.get("city") or {})),
        "area": _named(location.get("area") or {}), "description": description,
        "skills": skills, "category": roles,
        "industry": industry,
        "salary_min": _as_text(salary.get("min")), "salary_max": _as_text(salary.get("max")),
        "salary_currency": normalize_currency((salary.get("currency") or {}).get("code")),
        "salary_period": normalize_salary_period((salary.get("period") or {}).get("name")),
        "job_type": normalize_job_type(work_types),
        "work_mode": normalize_work_mode(_named(job.get("workplaceArrangement") or {})),
        "experience_level": _experience(job.get("workExperienceYears")) or _named(job.get("careerLevel") or {}),
        "gender_required": _as_text((job.get("candidatePreferences") or {}).get("gender")),
        "date_posted": _wuzzuf_date(job.get("postedAt")),
        "closing_date": _wuzzuf_date(job.get("expireAt")),
    }
    result["_authoritative_fields"] = tuple(
        field for field in ("category", "industry", "job_type", "work_mode")
        if result[field]
    )
    return result


def _section_content(soup, headings):
    heading = next((node for node in soup.find_all(["h2", "h3", "h4"])
                    if clean_text(node.get_text()).casefold() in headings), None)
    if not heading:
        return ""
    content = next((node for node in heading.find_next_siblings()
                    if getattr(node, "name", None) not in (None, "style")), None)
    return _html_text(content) if content else ""


def _wuzzuf_semantic_html(soup):
    """Target stable WUZZUF headings and browse-link URL semantics, not CSS hashes."""
    result = {}
    title = soup.find("h1")
    if title:
        result["title"] = clean_text(title.get_text(" ", strip=True))
        header = title.parent
        links = header.find_all("a", href=True) if header else []
        for link in links:
            href, value = link.get("href", ""), clean_text(link.get_text(" ", strip=True))
            if "filters%5Bcountry%5D" in href:
                if any(token in href for token in ("Full-Time", "Part-Time", "Contract", "Internship")):
                    result["job_type"] = value
                elif any(token in href for token in ("On-Site", "Remote", "Hybrid")):
                    result["work_mode"] = value
        location = next((clean_text(text) for text in header.stripped_strings
                         if "," in text and "posted" not in text.casefold()), "") if header else ""
        if location:
            city, country = (part.strip() for part in location.rsplit(",", 1))
            result.update(city=city, country=normalize_country(country))
    description = _section_content(soup, {"job description", "الوصف الوظيفي"})
    requirements = _section_content(soup, {"job requirements", "متطلبات الوظيفة"})
    description = description if _meaningful_text(description) else ""
    requirements = requirements if _meaningful_text(requirements) else ""
    if requirements:
        description = f"{description}\n\nJob Requirements\n{requirements}" if description else requirements
    if description:
        result["description"] = description
    company = next((node for node in soup.find_all(string=True)
                    if clean_text(node).casefold() in {"confidential company", "شركة سرية"}), None)
    if company:
        result["company_name"] = clean_text(company)
    return result


def _semantic_fallback(soup):
    result = {}
    itemprops = {
        "title": "title", "description": "description", "datePosted": "date_posted",
        "validThrough": "closing_date", "employmentType": "job_type",
    }
    for prop, field in itemprops.items():
        node = soup.select_one(f'[itemprop="{prop}"]')
        if node:
            result[field] = clean_text(node.get("content") or node.get_text(" ", strip=True))
    company = soup.select_one('[itemprop="hiringOrganization"] [itemprop="name"], [itemprop="hiringOrganization"]')
    if company:
        result["company_name"] = clean_text(company.get("content") or company.get_text(" ", strip=True))
    for node in soup.find_all(["dt", "th", "strong", "b"]):
        label = clean_text(node.get_text(" ", strip=True)).lower().rstrip(":")
        for field, aliases in LABELS.items():
            if label not in aliases:
                continue
            value_node = node.find_next_sibling(["dd", "td", "span", "div", "p"]) or node.find_next()
            if value_node:
                result[field] = clean_text(value_node.get_text(" ", strip=True))
    return result


def parse_wuzzuf_detail(html, url, source):
    soup = BeautifulSoup(html, "html.parser")
    posting = None
    for data in _json_scripts(soup, ld_only=True):
        posting = _jobposting(data)
        if posting:
            break
    if not posting:
        for data in _json_scripts(soup):
            posting = _jobposting(data)
            if posting:
                break
    values = _from_posting(posting) if posting else {}
    store_values = _from_wuzzuf_store(_wuzzuf_store(soup), url)
    authoritative_fields = set(store_values.get("_authoritative_fields", ()))
    for key, value in store_values.items():
        if key == "_authoritative_fields":
            continue
        if key in authoritative_fields or not values.get(key):
            values[key] = value
    if authoritative_fields:
        values["_authoritative_fields"] = tuple(
            field for field in ("category", "industry", "job_type", "work_mode")
            if field in authoritative_fields
        )
    live_html = _wuzzuf_semantic_html(soup)
    for key, value in live_html.items():
        if not values.get(key):
            values[key] = value
    fallback = _semantic_fallback(soup)
    for key, value in fallback.items():
        if not values.get(key):
            values[key] = value
    values["country"] = normalize_country(values.get("country")) or normalize_country(source.get("country"))
    values["job_type"] = normalize_job_type(values.get("job_type"))
    values["work_mode"] = normalize_work_mode(values.get("work_mode"))
    values["date_posted"] = normalize_date(values.get("date_posted"))
    values["closing_date"] = normalize_date(values.get("closing_date"))
    values["apply_url"] = canonical_url(url)
    values["source"] = source["name"]
    for field in JOB_FIELDS:
        values.setdefault(field, None if field in ("date_posted", "closing_date") else "")
    return values


EMPTY_TEXT_PLACEHOLDERS = {"n/a", "na", "not specified"}
REPLACEABLE_PROVIDER_FIELDS = {"lever": {"description"}}


def _meaningful_text(value):
    """Return whether text contains readable content rather than a placeholder."""
    text = clean_text(value)
    if not text or text.casefold().strip(" .,:;!?-_—–/\\()[]{}") in EMPTY_TEXT_PLACEHOLDERS:
        return False
    return any(character.isalnum() for character in text)


def validate_wuzzuf_detail(values):
    """Reject successful responses that contain only listing/config baselines."""
    substantial = (_meaningful_text(values.get("description"))
                   or _meaningful_text(values.get("company_name")))
    additional = any(_meaningful_text(values.get(field)) for field in (
        "city", "area", "skills", "category", "industry", "salary_min",
        "salary_max", "salary_currency", "salary_period", "job_type", "work_mode",
        "experience_level", "date_posted", "closing_date",
    ))
    return substantial and additional


def _missing(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _historical_company_placeholder(value):
    return isinstance(value, str) and value.strip().casefold() == "wuzzuf"


def _should_enrich(field, existing, scraped, authoritative=False):
    if _missing(scraped):
        return False
    if authoritative:
        return scraped != existing
    if field in ("description", "company_name"):
        if not _meaningful_text(scraped):
            return False
        if not _meaningful_text(existing):
            return True
    if _missing(existing):
        return True
    return (field == "company_name" and _historical_company_placeholder(existing)
            and not _historical_company_placeholder(scraped))


def _enrich_job(job, values):
    changed = False
    authoritative_fields = set(values.get("_authoritative_fields", ())) & {
        "category", "industry", "job_type", "work_mode",
    }
    replace_fields = set(values.get("_replace_fields", ())) & (
        REPLACEABLE_PROVIDER_FIELDS.get(values.get("_provider"), set())
    )
    if job.source != values.get("source"):
        replace_fields.clear()
    for field in JOB_FIELDS:
        scraped = values.get(field)
        if _should_enrich(
            field, getattr(job, field), scraped,
            field in authoritative_fields
            or (field in replace_fields and _meaningful_text(scraped)),
        ):
            setattr(job, field, scraped)
            changed = True
    return changed


def save_job(db, values):
    target_url = canonical_url(values["apply_url"])
    values["apply_url"] = target_url
    job = db.query(Job).filter(Job.apply_url == target_url).first()
    duplicate_count = 0
    if job:
        changed = _enrich_job(job, values)
        if changed:
            try:
                db.commit()
            except Exception:
                db.rollback()
                raise
            return "updated", duplicate_count
        return "unchanged", duplicate_count
    job = Job(**{field: values.get(field) for field in JOB_FIELDS})
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        job = db.query(Job).filter(Job.apply_url == target_url).first()
        if job is None:
            raise
        if _enrich_job(job, values):
            try:
                db.commit()
            except Exception:
                db.rollback()
                raise
            return "updated", duplicate_count
        return "unchanged", duplicate_count
    except Exception:
        db.rollback()
        raise
    return "inserted", duplicate_count


def normalize_lever_job_type(value):
    """Normalize only unambiguous Lever commitment labels."""
    value = clean_text(value)
    comparable = value.casefold().replace("_", " ")
    if re.search(r"(?:^|[\s,])full[ -]?time(?:$|[\s,])", comparable):
        return "Full Time"
    if re.search(r"(?:^|[\s,])part[ -]?time(?:$|[\s,])", comparable):
        return "Part Time"
    if comparable in {"intern", "internship"}:
        return "Internship"
    if re.search(r"\b(contract|contractor|contractual)\b", comparable):
        return "Contract"
    if re.search(r"\b(temporary|temp)\b", comparable):
        return "Temporary"
    return value


def _lever_salary(value):
    if not isinstance(value, dict):
        return "", "", "", ""
    return (
        _as_text(value.get("min")),
        _as_text(value.get("max")),
        normalize_currency(value.get("currency")),
        normalize_salary_period(value.get("interval")),
    )


def _lever_plain_text(value):
    """Clean a plain-text description without discarding useful line breaks."""
    if not isinstance(value, str):
        return ""
    lines = [clean_text(line) for line in str(value).splitlines()]
    result = []
    blank = True
    for line in lines:
        if line:
            result.append(line)
            blank = False
        elif not blank:
            result.append("")
            blank = True
    return "\n".join(result).strip()


def _lever_html_text(value):
    """Convert Lever section HTML to readable text with visible list items."""
    if not isinstance(value, str):
        return ""
    soup = BeautifulSoup(value, "html.parser")
    for line_break in soup.find_all("br"):
        line_break.replace_with("\n")
    for item in reversed(soup.find_all("li")):
        item.replace_with(f"- {clean_text(item.get_text(' ', strip=True))}\n")
    for block in soup.find_all(["p", "div", "section"]):
        block.insert_before("\n\n")
        block.append("\n\n")

    lines = [clean_text(line) for line in soup.get_text().splitlines()]
    paragraphs = []
    blank = True
    for line in lines:
        if line:
            paragraphs.append(line)
            blank = False
        elif not blank:
            paragraphs.append("")
            blank = True
    return "\n".join(paragraphs).strip()


def _lever_description(posting):
    """Assemble the complete published Lever description in feed order."""
    components = []
    seen = set()

    def append(value):
        value = value.strip()
        comparable = "\n".join(line.rstrip() for line in value.splitlines()).strip()
        if _meaningful_text(value) and comparable not in seen:
            seen.add(comparable)
            components.append(value)

    append(_lever_plain_text(posting.get("descriptionPlain")))
    sections = posting.get("lists")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            heading = _lever_plain_text(section.get("text"))
            content = _lever_html_text(section.get("content"))
            if _meaningful_text(heading) and _meaningful_text(content):
                append(f"{heading}\n\n{content}")
    append(_lever_plain_text(posting.get("additionalPlain")))
    return "\n\n".join(components)


def _valid_external_url(value):
    parts = urlsplit(value)
    return parts.scheme.lower() in {"http", "https"} and bool(parts.hostname)


def parse_lever_posting(posting, source):
    """Map one Lever posting, returning None when required data is invalid."""
    if not isinstance(posting, dict) or not clean_text(source.get("company_name")):
        return None
    title = clean_text(posting.get("text"))
    raw_country = clean_text(posting.get("country")).casefold()
    # Lever eligibility is intentionally limited to explicit country codes.
    explicit_countries = {"sa", "ae", "qa", "kw", "bh", "om"}
    if raw_country not in explicit_countries:
        return None
    country = normalize_country(raw_country)
    apply_url = canonical_url(posting.get("applyUrl"))
    if (not title or not country or not apply_url
            or not _valid_external_url(apply_url)):
        return None
    categories = posting.get("categories")
    categories = categories if isinstance(categories, dict) else {}
    salary_min, salary_max, currency, period = _lever_salary(posting.get("salaryRange"))
    description = _lever_description(posting)
    values = {
        "title": title,
        "company_name": clean_text(source["company_name"]),
        "country": country,
        "city": clean_text(categories.get("location")),
        "description": description,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": currency,
        "salary_period": period,
        "job_type": normalize_lever_job_type(categories.get("commitment")),
        "work_mode": normalize_work_mode(posting.get("workplaceType"))
        if clean_text(posting.get("workplaceType")).casefold()
        in {"onsite", "on-site", "on site", "hybrid", "remote"} else "",
        "apply_url": apply_url,
        "source": source["name"],
        "_provider": "lever",
    }
    if _meaningful_text(description):
        values["_replace_fields"] = ("description",)
    for field in JOB_FIELDS:
        values.setdefault(field, None if field in ("date_posted", "closing_date") else "")
    return values


def parse_lever_feed(text, source):
    """Parse a complete Lever JSON feed while isolating malformed postings."""
    postings = json.loads(text)
    if not isinstance(postings, list):
        raise ValueError("Lever feed must contain a JSON list")
    jobs = []
    for posting in postings:
        try:
            values = parse_lever_posting(posting, source)
            if values:
                jobs.append(values)
        except (TypeError, ValueError):
            continue
    return jobs, len(postings)


def _import_lever_source(source, response, totals, session_factory):
    jobs, posting_count = parse_lever_feed(response.text, source)
    totals["listing_links_found"] += posting_count
    unique = {job["apply_url"]: job for job in jobs}
    totals["unique_job_urls"] += len(unique)
    for values in unique.values():
        db = session_factory()
        try:
            outcome, duplicate_count = save_job(db, values)
            totals[outcome] += 1
            totals["duplicate_database_urls"] += duplicate_count
        finally:
            db.close()


def dispatch_listing_parser(source, html):
    parser_identity = source.get("listing_parser")
    if parser_identity == "wuzzuf":
        return (parse_wuzzuf_listing(html, source["url"]),
                count_wuzzuf_listing_links(html, source["url"]))
    raise UnsupportedParserError(f"Unsupported listing parser: {parser_identity!r}")


def get_detail_parser(source):
    parser_identity = source.get("detail_parser")
    if parser_identity == "wuzzuf":
        return parse_wuzzuf_detail
    raise UnsupportedParserError(f"Unsupported detail parser: {parser_identity!r}")


def _wuzzuf_source():
    """Return the configured WUZZUF parser settings used for polite requests."""
    return next((source for source in SOURCES
                 if source.get("name") == "WUZZUF"
                 and source.get("detail_parser") == "wuzzuf"), None)


def backfill_wuzzuf_filters(session_factory=Session, http_session=None,
                            sleeper=time.sleep):
    """Repair only classifications backed by WUZZUF's structured job entity.

    Rows are selected by source, then revisited through their stored application
    URL. Each row has its own transaction so a request, parse, or database failure
    cannot prevent later rows from being considered. Failures are returned only
    as aggregate categories; URLs, response contents, and request data are not
    included in the summary.
    """
    totals = Counter(scanned=0, updated=0, unchanged=0,
                     skipped_missing_page=0,
                     skipped_no_authoritative_data=0, failed=0)
    diagnostics = Counter({name: 0 for name in WUZZUF_BACKFILL_FAILURE_DIAGNOSTICS})
    source = _wuzzuf_source()
    if source is None:
        raise UnsupportedParserError("WUZZUF detail source is not configured")

    db = session_factory()
    try:
        rows = db.query(Job.id, Job.apply_url).filter(Job.source == source["name"]).all()
    finally:
        db.close()

    http = http_session or requests.Session()
    totals["scanned"] = len(rows)
    for index, (job_id, stored_url) in enumerate(rows):
        failure_stage = "parser_or_unexpected"
        try:
            target_url = canonical_url(stored_url)
            if (target_url != stored_url
                    or not _is_wuzzuf_job_url(target_url, WUZZUF_HOSTS)):
                totals["skipped_no_authoritative_data"] += 1
                continue
            response = _request(http, target_url, source)
            values = parse_wuzzuf_detail(response.text, target_url, source)
            authoritative = set(values.get("_authoritative_fields", ()))
            repairs = {
                field: values[field] for field in WUZZUF_FILTER_FIELDS
                if field in authoritative and not _missing(values.get(field))
            }
            if not repairs:
                totals["skipped_no_authoritative_data"] += 1
                continue

            failure_stage = "database"
            db = session_factory()
            try:
                job = db.query(Job).filter(
                    Job.id == job_id,
                    Job.source == source["name"],
                    Job.apply_url == stored_url,
                ).first()
                if job is not None:
                    changed = False
                    for field, value in repairs.items():
                        if getattr(job, field) != value:
                            setattr(job, field, value)
                            changed = True
                    if changed:
                        db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
                raise
            finally:
                db.close()
            if job is None:
                totals["failed"] += 1
                diagnostics["database"] += 1
            elif changed:
                totals["updated"] += 1
            else:
                totals["unchanged"] += 1
        except requests.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                totals["skipped_missing_page"] += 1
            else:
                totals["failed"] += 1
                if status == 403:
                    diagnostics["http_403"] += 1
                elif status == 429:
                    diagnostics["http_429"] += 1
                elif status is not None and 500 <= status < 600:
                    diagnostics["http_5xx"] += 1
                else:
                    diagnostics["http_other"] += 1
        except requests.Timeout:
            totals["failed"] += 1
            diagnostics["timeout"] += 1
        except requests.ConnectionError:
            totals["failed"] += 1
            diagnostics["connection_error"] += 1
        except Exception:
            totals["failed"] += 1
            diagnostics[failure_stage] += 1
        finally:
            if index + 1 < len(rows):
                sleeper(max(0, float(source.get("polite_delay", 0))))
    return {**totals, "failure_diagnostics": dict(diagnostics)}


def run_import(session_factory=Session, http_session=None, sleeper=time.sleep):
    print("Starting import...")
    totals = Counter(listing_links_found=0, unique_job_urls=0, inserted=0, updated=0,
                     unchanged=0, failed_detail_pages=0, duplicate_database_urls=0)
    http = http_session or requests.Session()
    for source in SOURCES:
        if not source.get("active"):
            continue
        print(f"Processing source: {source['name']}")
        try:
            response = _request(http, source["url"], source)
            if source.get("provider") == "lever":
                _import_lever_source(source, response, totals, session_factory)
                continue
            jobs, listing_count = dispatch_listing_parser(source, response.text)
            detail_parser = get_detail_parser(source)
            totals["listing_links_found"] += listing_count
        except UnsupportedParserError as exc:
            print(f"Source skipped: {source['name']}: {exc}")
            continue
        except Exception as exc:
            print(f"Source failed: {source['name']}: {exc}")
            continue
        unique = {job["link"]: job for job in jobs}
        totals["unique_job_urls"] += len(unique)
        for index, job in enumerate(unique.values()):
            try:
                detail = _request(http, job["link"], source)
                values = detail_parser(detail.text, job["link"], source)
                if not values.get("title"):
                    values["title"] = job["title"]
                if source.get("detail_parser") == "wuzzuf" and not validate_wuzzuf_detail(values):
                    raise ValueError("no meaningful WUZZUF detail enrichment")
                detail_authoritative = set(values.get("_authoritative_fields", ()))
                listing_authoritative = set(job.get("_authoritative_fields", ())) & {
                    "category", "job_type", "work_mode",
                }
                if listing_authoritative:
                    values.update({field: job[field] for field in listing_authoritative})
                    values["_authoritative_fields"] = tuple(
                        field for field in WUZZUF_FILTER_FIELDS
                        if field in detail_authoritative | listing_authoritative
                    )
                db = session_factory()
                try:
                    outcome, duplicate_count = save_job(db, values)
                    totals[outcome] += 1
                    totals["duplicate_database_urls"] += duplicate_count
                finally:
                    db.close()
            except Exception as exc:
                totals["failed_detail_pages"] += 1
                repairs = {
                    field: job[field]
                    for field in set(job.get("_authoritative_fields", ()))
                    & {"category", "job_type", "work_mode"}
                    if not _missing(job.get(field))
                }
                if repairs and source.get("detail_parser") == "wuzzuf":
                    db = session_factory()
                    try:
                        existing = db.query(Job).filter(
                            Job.apply_url == canonical_url(job["link"]),
                            Job.source == source["name"],
                        ).first()
                        if existing is not None:
                            changed = any(getattr(existing, field) != value
                                          for field, value in repairs.items())
                            for field, value in repairs.items():
                                setattr(existing, field, value)
                            if changed:
                                db.commit()
                                totals["updated"] += 1
                            else:
                                totals["unchanged"] += 1
                    except Exception:
                        db.rollback()
                    finally:
                        db.close()
                print(f"Detail failed: {job['link']}: {exc}")
            if index + 1 < len(unique):
                sleeper(max(0, float(source.get("polite_delay", 0))))
    print("Import summary:")
    for key in ("listing_links_found", "unique_job_urls", "inserted", "updated", "unchanged",
                "failed_detail_pages", "duplicate_database_urls"):
        print(f"  {key}: {totals[key]}")
    return dict(totals)


if __name__ == "__main__":
    run_import()
