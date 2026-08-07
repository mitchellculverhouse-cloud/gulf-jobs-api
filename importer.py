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
            jobs.append({"title": title, "link": url})
        except (KeyError, TypeError, ValueError):
            continue
    return jobs


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
    for field in JOB_FIELDS:
        scraped = values.get(field)
        if _should_enrich(
            field, getattr(job, field), scraped, field in authoritative_fields
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
                db = session_factory()
                try:
                    outcome, duplicate_count = save_job(db, values)
                    totals[outcome] += 1
                    totals["duplicate_database_urls"] += duplicate_count
                finally:
                    db.close()
            except Exception as exc:
                totals["failed_detail_pages"] += 1
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
