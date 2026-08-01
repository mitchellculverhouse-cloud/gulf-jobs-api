from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Annotated, Optional

from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Numeric, case, cast, func, or_

from database import Session, init_db
from importer import run_import
from models import Job
from schemas import JobResult, PaginatedJobsResponse


LIKE_ESCAPE = "\\"
JOB_RESULT_FIELDS = tuple(JobResult.model_fields)


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


@app.get("/jobs/{job_id}", response_model=JobResult)
def get_job(job_id: Annotated[int, Path(ge=1)]):
    with Session() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job_to_dict(job)



@app.get("/run-import")
def run_import_test():

    run_import()

    with Session() as session:
        count = session.query(Job).count()

    return {
        "status": "import complete",
        "total_jobs": count
    }
