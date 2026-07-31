import json
import time
from collections import Counter

import feedparser
import requests
from bs4 import BeautifulSoup

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


def parse_wuzzuf_listing(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        try:
            url = canonical_url(anchor["href"], base_url)
            if "/jobs/p/" not in urlsplit_path(url) or url in seen:
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
    for anchor in soup.find_all("a", href=True):
        try:
            url = canonical_url(anchor["href"], base_url)
            if "/jobs/p/" in urlsplit_path(url) and clean_text(anchor.get_text(" ", strip=True)):
                count += 1
        except (KeyError, TypeError, ValueError):
            continue
    return count


def urlsplit_path(url):
    from urllib.parse import urlsplit
    return urlsplit(url).path


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
        "description": clean_text(BeautifulSoup(str(posting.get("description", "")), "html.parser").get_text(" ")),
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


def _missing(value):
    return value is None or (isinstance(value, str) and not value.strip())


def save_job(db, values):
    target_url = canonical_url(values["apply_url"])
    values["apply_url"] = target_url
    matches = [job for job in db.query(Job).filter(Job.apply_url.isnot(None)).order_by(Job.id).all()
               if canonical_url(job.apply_url) == target_url]
    duplicate_count = max(0, len(matches) - 1)
    if matches:
        job = matches[0]
        changed = False
        for field in JOB_FIELDS:
            scraped = values.get(field)
            if _missing(getattr(job, field)) and not _missing(scraped):
                setattr(job, field, scraped)
                changed = True
        if changed:
            db.commit()
            return "updated", duplicate_count
        return "unchanged", duplicate_count
    job = Job(**{field: values.get(field) for field in JOB_FIELDS})
    db.add(job)
    db.commit()
    return "inserted", duplicate_count


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
            if source.get("type") == "rss":
                entries = feedparser.parse(response.text).entries
                jobs = [{"title": clean_text(item.get("title")), "link": canonical_url(item.get("link"), source["url"])} for item in entries]
                jobs = [job for job in jobs if job["title"] and job["link"]]
            elif source.get("listing_parser") == "wuzzuf" or source.get("type") == "html":
                jobs = parse_wuzzuf_listing(response.text, source["url"])
                totals["listing_links_found"] += count_wuzzuf_listing_links(response.text, source["url"])
            else:
                print(f"Unknown source type: {source.get('type')}")
                continue
        except Exception as exc:
            print(f"Source failed: {source['name']}: {exc}")
            continue
        if source.get("type") == "rss":
            totals["listing_links_found"] += len(jobs)
        unique = {job["link"]: job for job in jobs}
        totals["unique_job_urls"] += len(unique)
        for index, job in enumerate(unique.values()):
            try:
                detail = _request(http, job["link"], source)
                values = parse_wuzzuf_detail(detail.text, job["link"], source)
                if not values.get("title"):
                    values["title"] = job["title"]
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
