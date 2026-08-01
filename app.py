from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import or_
from datetime import datetime, timedelta
from database import Session, init_db
from importer import run_import
from models import Job

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


@app.get("/jobs")
def get_jobs(
    search: str = None,

    location: str = None,

    category: str = None,
    industry: str = None,

    min_salary: int = None,
    currency: str = None,
    salary_period: str = None,

    job_type: str = None,
    work_mode: str = None,
    experience_level: str = None,

    nationality: str = None,
    gender: str = None,
    language: str = None,

    remote_only: bool = False,
    arabic_only: bool = False,

    date_range: int = None,

    sort: str = "newest",

    page: int = 1,
    limit: int = 25
):

    with Session() as session:
        query = session.query(Job)

        if search:
            text = f"%{search}%"

            query = query.filter(
                or_(
                    Job.title.ilike(text),
                    Job.description.ilike(text),
                    Job.skills.ilike(text),
                    Job.company_name.ilike(text)
                )
            )

        if location:
            text = f"%{location}%"

            query = query.filter(
                or_(
                    Job.country.ilike(text),
                    Job.city.ilike(text),
                    Job.area.ilike(text)
                )
            )

        if category:
            query = query.filter(
                Job.category.ilike(f"%{category}%")
            )

        if industry:
            query = query.filter(
                Job.industry.ilike(f"%{industry}%")
            )

        if currency:
            query = query.filter(
                Job.salary_currency.ilike(f"%{currency}%")
            )

        if salary_period:
            query = query.filter(
                Job.salary_period.ilike(f"%{salary_period}%")
            )

        if min_salary:
            query = query.filter(
                Job.salary_max >= str(min_salary)
            )

        if job_type:
            query = query.filter(Job.job_type == job_type)

        if work_mode:
            query = query.filter(Job.work_mode == work_mode)

        if experience_level:
            query = query.filter(
                Job.experience_level == experience_level
            )

        if nationality:
            query = query.filter(
                Job.nationality_required == nationality
            )

        if gender:
            query = query.filter(
                Job.gender_required == gender
            )

        if language:
            query = query.filter(
                Job.languages_required.ilike(f"%{language}%")
            )

        if remote_only:
            query = query.filter(
                Job.work_mode == "Remote"
            )

        if arabic_only:
            query = query.filter(
                Job.arabic_required == "Yes"
            )

        if date_range:
            cutoff = datetime.now() - timedelta(days=date_range)

            query = query.filter(
                Job.date_posted >= cutoff.strftime("%Y-%m-%d")
            )

        if sort == "highest_salary":
            query = query.order_by(
                Job.salary_max.desc()
            )

        elif sort == "lowest_salary":
            query = query.order_by(
                Job.salary_min.asc()
            )

        else:
            query = query.order_by(
                Job.date_posted.desc()
            )

        offset = (page - 1) * limit

        jobs = (
            query
            .offset(offset)
            .limit(limit)
            .all()
        )

        results = []

        for job in jobs:

            results.append({

                "id": job.id,

                "title": job.title,
                "description": job.description,
                "skills": job.skills,

                "country": job.country,
                "city": job.city,
                "area": job.area,

                "company_name": job.company_name,

                "category": job.category,
                "industry": job.industry,

                "salary_min": job.salary_min,
                "salary_max": job.salary_max,
                "salary_currency": job.salary_currency,
                "salary_period": job.salary_period,

                "job_type": job.job_type,
                "work_mode": job.work_mode,
                "experience_level": job.experience_level,

                "nationality_required": job.nationality_required,
                "gender_required": job.gender_required,
                "arabic_required": job.arabic_required,
                "languages_required": job.languages_required,

                "date_posted": job.date_posted,
                "closing_date": job.closing_date,

                "apply_url": job.apply_url,
                "source": job.source
            })


        return {
            "page": page,
            "limit": limit,
            "results": results
        }



@app.get("/run-import")
def run_import_test():

    run_import()

    with Session() as session:
        count = session.query(Job).count()

    return {
        "status": "import complete",
        "total_jobs": count
    }
