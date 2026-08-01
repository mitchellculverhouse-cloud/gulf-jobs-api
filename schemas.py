from typing import Optional

from pydantic import BaseModel, ConfigDict


class JobResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: Optional[str] = None
    description: Optional[str] = None
    skills: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    area: Optional[str] = None
    company_name: Optional[str] = None
    category: Optional[str] = None
    industry: Optional[str] = None
    salary_min: Optional[str] = None
    salary_max: Optional[str] = None
    salary_currency: Optional[str] = None
    salary_period: Optional[str] = None
    job_type: Optional[str] = None
    work_mode: Optional[str] = None
    experience_level: Optional[str] = None
    nationality_required: Optional[str] = None
    gender_required: Optional[str] = None
    arabic_required: Optional[str] = None
    languages_required: Optional[str] = None
    date_posted: Optional[str] = None
    closing_date: Optional[str] = None
    apply_url: Optional[str] = None
    source: Optional[str] = None

class PaginatedJobsResponse(BaseModel):
    page: int
    limit: int
    total: int
    total_pages: int
    has_next: bool
    has_previous: bool
    results: list[JobResult]
