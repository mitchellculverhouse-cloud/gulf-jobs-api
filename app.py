import hmac
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Annotated, Optional

from fastapi import FastAPI, Header, HTTPException, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Numeric, case, cast, func, or_, text

from database import Session, init_db
from importer import (
    WUZZUF_CATEGORIES, WUZZUF_JOB_TYPES, WUZZUF_WORK_MODES,
    backfill_wuzzuf_filters, run_import,
)
from models import Job
from schemas import JobFilterOptionsResponse, JobResult, PaginatedJobsResponse


LIKE_ESCAPE = "\\"
JOB_RESULT_FIELDS = tuple(JobResult.model_fields)
IMPORT_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


class JobSort(str, Enum):
    newest = "newest"
    oldest = "oldest"
    highest_salary = "highest_salary"
    lowest_salary = "lowest_salary"


def escaped_contains(value: Optional[str]) -> Optional[str]:
    """Return a trimmed, escaped pattern for a literal substring search."""
    if value is None or not value.strip():
        return None
    value = value.strip()
    value = value.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
    value = value.replace("%", LIKE_ESCAPE + "%")
    value = value.replace("_", LIKE_ESCAPE + "_")
    return f"%{value}%"


def trimmed(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def job_to_dict(job: Job):
    """Serialize only fields deliberately included in the public job contract."""
    return {field: getattr(job, field) for field in JOB_RESULT_FIELDS}


def cleaned_distinct_values(session, column):
    """Return consistently sorted, unique, non-blank values for one job column."""
    values = session.query(func.trim(column)).filter(column.isnot(None)).distinct().all()
    return sorted({value.strip() for value, in values if value and value.strip()})


def cleaned_delimited_values(session, column):
    """Expand importer-delimited multi-values into usable text-filter options."""
    values = cleaned_distinct_values(session, column)
    return sorted({part.strip() for value in values for part in value.split(",") if part.strip()})


WUZZUF_EXPERIENCE = re.compile(
    r"^(?:\d+\+ years|\d+\s*-\s*\d+ years|Up to \d+ years)$",
    re.IGNORECASE,
)


def trustworthy_filter_values(session, column, delimited=False, allowed=None):
    """Hide legacy WUZZUF taxonomy while retaining trustworthy source values.

    Industry has no trustworthy listing source, so callers omit every WUZZUF
    industry. Exact classifications use the platform's finite normalized
    vocabulary. Categories reject the characteristic undelimited CamelCase
    joins produced by the legacy parser; values are never split heuristically.
    """
    rows = session.query(column, Job.source).filter(column.isnot(None)).all()
    values = set()
    for raw, source in rows:
        value = (raw or "").strip()
        if not value:
            continue
        if source == "WUZZUF":
            if allowed is None and column is Job.industry:
                continue
            parts = [part.strip() for part in value.split(",") if part.strip()]
            if allowed is not None and (not parts or any(part not in allowed for part in parts)):
                continue
            if delimited and column is Job.category and any(
                    part not in WUZZUF_CATEGORIES for part in parts):
                continue
        else:
            parts = [part.strip() for part in value.split(",") if part.strip()]
        values.update(parts if delimited else [value])
    return sorted(values)


def trustworthy_experience_values(session):
    rows = session.query(Job.experience_level, Job.source).filter(
        Job.experience_level.isnot(None)).all()
    return sorted({value.strip() for value, source in rows if value and value.strip()
                   and (source != "WUZZUF" or WUZZUF_EXPERIENCE.fullmatch(value.strip()))})


def numeric_salary(column, dialect_name):
    """Safely convert valid non-negative salary text to a numeric expression."""
    value = func.trim(column)
    if dialect_name == "postgresql":
        valid = value.op("~")(r"^[0-9]+(\.[0-9]+)?$")
    else:
        valid = (
            (value != "")
            & ~value.op("GLOB")("*[^0-9.]*")
            & ~value.op("GLOB")(".*")
            & ~value.op("GLOB")("*.")
            & ((func.length(value) - func.length(func.replace(value, ".", ""))) <= 1)
        )
    return case((valid, cast(value, Numeric)), else_=None)


def utc_today():
    return datetime.now(timezone.utc).date()


def date_order(descending=True):
    value = func.nullif(func.trim(Job.date_posted), "")
    return (value.desc().nullslast() if descending else value.asc().nullslast())

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://jobsultan.odoo.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def initialize_database():
    init_db()


@app.get("/health")
def health():
    session = Session()
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    finally:
        session.close()
    return {"status": "ok"}


@app.get("/jobs", response_model=PaginatedJobsResponse)
def get_jobs(
    search: Annotated[Optional[str], Query(max_length=200)] = None,

    location: Annotated[Optional[str], Query(max_length=100)] = None,

    category: Annotated[Optional[str], Query(max_length=100)] = None,
    industry: Annotated[Optional[str], Query(max_length=100)] = None,

    min_salary: Annotated[Optional[float], Query(ge=0)] = None,
    currency: Annotated[Optional[str], Query(max_length=20)] = None,
    salary_period: Annotated[Optional[str], Query(max_length=50)] = None,

    job_type: Annotated[Optional[str], Query(max_length=50)] = None,
    work_mode: Annotated[Optional[str], Query(max_length=50)] = None,
    experience_level: Annotated[Optional[str], Query(max_length=100)] = None,

    nationality: Annotated[Optional[str], Query(max_length=100)] = None,
    gender: Annotated[Optional[str], Query(max_length=50)] = None,
    language: Annotated[Optional[str], Query(max_length=100)] = None,

    remote_only: bool = False,
    arabic_only: bool = False,

    date_range: Annotated[Optional[int], Query(ge=1, le=3650)] = None,

    sort: JobSort = JobSort.newest,

    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 25
):

    with Session() as session:
        query = session.query(Job)

        text = escaped_contains(search)
        if text:
            query = query.filter(
                or_(
                    Job.title.ilike(text, escape=LIKE_ESCAPE),
                    Job.description.ilike(text, escape=LIKE_ESCAPE),
                    Job.skills.ilike(text, escape=LIKE_ESCAPE),
                    Job.company_name.ilike(text, escape=LIKE_ESCAPE)
                )
            )

        text = escaped_contains(location)
        if text:
            query = query.filter(
                or_(
                    Job.country.ilike(text, escape=LIKE_ESCAPE),
                    Job.city.ilike(text, escape=LIKE_ESCAPE),
                    Job.area.ilike(text, escape=LIKE_ESCAPE)
                )
            )

        for value, column in (
            (category, Job.category),
            (industry, Job.industry),
            (currency, Job.salary_currency),
            (salary_period, Job.salary_period),
            (language, Job.languages_required),
        ):
            text = escaped_contains(value)
            if text:
                query = query.filter(column.ilike(text, escape=LIKE_ESCAPE))

        dialect_name = session.get_bind().dialect.name
        salary_max = numeric_salary(Job.salary_max, dialect_name)
        salary_min = numeric_salary(Job.salary_min, dialect_name)
        if min_salary is not None:
            query = query.filter(salary_max >= min_salary)

        for value, column in (
            (job_type, Job.job_type),
            (work_mode, Job.work_mode),
            (experience_level, Job.experience_level),
            (nationality, Job.nationality_required),
            (gender, Job.gender_required),
        ):
            value = trimmed(value)
            if value:
                query = query.filter(column == value)

        if remote_only:
            query = query.filter(
                Job.work_mode == "Remote"
            )

        if arabic_only:
            query = query.filter(
                Job.arabic_required == "Yes"
            )

        if date_range:
            cutoff = utc_today() - timedelta(days=date_range)
            query = query.filter(Job.date_posted >= cutoff.isoformat())

        total = query.count()

        if sort == JobSort.highest_salary:
            query = query.order_by(
                salary_max.desc().nullslast(), date_order(), Job.id.desc()
            )
        elif sort == JobSort.lowest_salary:
            query = query.order_by(
                salary_min.asc().nullslast(), date_order(), Job.id.desc()
            )
        elif sort == JobSort.oldest:
            query = query.order_by(date_order(False), Job.id.asc())
        else:
            query = query.order_by(date_order(), Job.id.desc())

        offset = (page - 1) * limit

        jobs = (
            query
            .offset(offset)
            .limit(limit)
            .all()
        )

        total_pages = (total + limit - 1) // limit
        return {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_previous": page > 1 and total > 0,
            "results": [job_to_dict(job) for job in jobs],
        }


@app.get("/jobs/filter-options", response_model=JobFilterOptionsResponse)
def get_job_filter_options():
    with Session() as session:
        return {
            "countries": cleaned_distinct_values(session, Job.country),
            "cities": cleaned_distinct_values(session, Job.city),
            "categories": trustworthy_filter_values(session, Job.category, delimited=True),
            "industries": trustworthy_filter_values(session, Job.industry, delimited=True),
            "job_types": trustworthy_filter_values(session, Job.job_type, allowed=WUZZUF_JOB_TYPES),
            "work_modes": trustworthy_filter_values(session, Job.work_mode, allowed=WUZZUF_WORK_MODES),
            "experience_levels": trustworthy_experience_values(session),
            "currencies": cleaned_distinct_values(session, Job.salary_currency),
            "salary_periods": cleaned_distinct_values(session, Job.salary_period),
            "languages": cleaned_distinct_values(session, Job.languages_required),
        }


@app.get("/jobs/{job_id}", response_model=JobResult)
def get_job(job_id: Annotated[int, Path(ge=1)]):
    with Session() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job_to_dict(job)


def require_import_key(x_import_key):
    """Apply the shared secret check used by protected maintenance operations."""
    expected_key = os.environ.get("IMPORT_API_KEY")
    if not expected_key:
        raise HTTPException(status_code=503, detail="Import endpoint is not configured")
    if not x_import_key or not hmac.compare_digest(x_import_key, expected_key):
        raise HTTPException(status_code=401, detail="Invalid import key")


@app.post("/run-import")
def run_import_endpoint(x_import_key: Annotated[Optional[str], Header()] = None):
    require_import_key(x_import_key)
    if not IMPORT_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Import already running")

    try:
        summary = run_import() or {}
    except Exception:
        logger.exception("Import failed")
        raise HTTPException(status_code=500, detail="Import failed") from None
    finally:
        IMPORT_LOCK.release()

    return {"status": "completed", **summary}


@app.post("/maintenance/backfill-wuzzuf-filters")
def backfill_wuzzuf_filters_endpoint(
    x_import_key: Annotated[Optional[str], Header()] = None,
):
    require_import_key(x_import_key)
    if not IMPORT_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Import already running")
    try:
        summary = backfill_wuzzuf_filters()
    except Exception:
        logger.exception("WUZZUF filter backfill failed")
        raise HTTPException(status_code=500, detail="WUZZUF filter backfill failed") from None
    finally:
        IMPORT_LOCK.release()
    return {"status": "completed", **summary}
