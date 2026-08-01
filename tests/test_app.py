import asyncio
import json
from datetime import date
from urllib.parse import urlencode

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app as app_module
from models import Base, Job


RESULT_FIELDS = {
    "id", "title", "description", "skills", "country", "city", "area",
    "company_name", "category", "industry", "salary_min", "salary_max",
    "salary_currency", "salary_period", "job_type", "work_mode",
    "experience_level", "nationality_required", "gender_required",
    "arabic_required", "languages_required", "date_posted", "closing_date",
    "apply_url", "source",
}


async def _asgi_get(path, params=None):
    messages = []
    query_string = urlencode(params or {}, doseq=True).encode()
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": query_string,
        "headers": [], "client": ("test", 123), "server": ("test", 80),
        "root_path": "",
    }
    await app_module.app(scope, receive, send)
    status = next(message["status"] for message in messages
                  if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages
                    if message["type"] == "http.response.body")
    return status, json.loads(body)


def get(path="/jobs", **params):
    return asyncio.run(_asgi_get(path, params))


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'jobs.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(app_module, "Session", factory)
    yield factory
    engine.dispose()


def add_jobs(session_factory, *rows):
    defaults = {
        "title": "Engineer", "description": "Build systems", "skills": "Python",
        "country": "UAE", "city": "Dubai", "area": "Marina",
        "company_name": "Acme", "category": "Technology", "industry": "Software",
        "salary_min": "900", "salary_max": "10000", "salary_currency": "AED",
        "salary_period": "Monthly", "job_type": "Full Time", "work_mode": "On-site",
        "experience_level": "Mid", "nationality_required": "Any",
        "gender_required": "Any", "arabic_required": "No",
        "languages_required": "English", "date_posted": "2026-07-20",
        "closing_date": None, "source": "Test",
    }
    with session_factory() as session:
        for number, values in enumerate(rows, 1):
            data = defaults | values
            data.setdefault("apply_url", f"https://example.test/{number}")
            session.add(Job(**data))
        session.commit()


def test_response_contract_and_result_fields(session_factory):
    add_jobs(session_factory, {})
    status, body = get()
    assert status == 200
    assert set(body) == {
        "page", "limit", "total", "total_pages", "has_next", "has_previous", "results"
    }
    assert (body["page"], body["limit"], body["total"], body["total_pages"]) == (1, 25, 1, 1)
    assert body["has_next"] is False and body["has_previous"] is False
    assert set(body["results"][0]) == RESULT_FIELDS


def test_filtered_totals_zero_results_and_out_of_range_pages(session_factory):
    add_jobs(session_factory, {"country": "UAE"}, {"country": "Qatar"}, {"country": "UAE"})
    assert get(location="UAE", limit=1)[1] | {"results": []} == {
        "page": 1, "limit": 1, "total": 2, "total_pages": 2,
        "has_next": True, "has_previous": False, "results": [],
    }
    zero = get(search="absent")[1]
    assert zero["total"] == zero["total_pages"] == 0
    assert zero["results"] == [] and zero["has_previous"] is False
    beyond = get(location="UAE", limit=1, page=3)[1]
    assert beyond["results"] == [] and beyond["has_next"] is False
    assert beyond["has_previous"] is True


@pytest.mark.parametrize("params", [
    {"page": 0}, {"page": -1}, {"page": 10001}, {"limit": 0}, {"limit": 101},
    {"min_salary": -1}, {"date_range": 0}, {"date_range": 3651},
    {"sort": "popular"}, {"search": "x" * 201}, {"location": "x" * 101},
    {"category": "x" * 101}, {"industry": "x" * 101}, {"currency": "x" * 21},
    {"salary_period": "x" * 51}, {"job_type": "x" * 51},
    {"work_mode": "x" * 51}, {"experience_level": "x" * 101},
    {"nationality": "x" * 101}, {"gender": "x" * 51},
    {"language": "x" * 101},
])
def test_query_validation_returns_422(session_factory, params):
    assert get(**params)[0] == 422


def test_whitespace_only_values_are_ignored(session_factory):
    add_jobs(session_factory, {}, {"title": "Other"})
    status, body = get(search="   ", location="  ", job_type=" ")
    assert status == 200 and body["total"] == 2


@pytest.mark.parametrize(("parameter", "value", "field"), [
    ("search", "needle", "title"), ("search", "needle", "description"),
    ("search", "needle", "skills"), ("search", "needle", "company_name"),
    ("location", "needle", "country"), ("location", "needle", "city"),
    ("location", "needle", "area"), ("category", "needle", "category"),
    ("industry", "needle", "industry"), ("currency", "needle", "salary_currency"),
    ("salary_period", "needle", "salary_period"),
    ("language", "needle", "languages_required"),
])
def test_partial_filters_are_case_insensitive(session_factory, parameter, value, field):
    add_jobs(session_factory, {field: "Prefix NEEDLE suffix"}, {field: "other"})
    assert get(**{parameter: value})[1]["total"] == 1


@pytest.mark.parametrize(("parameter", "field", "value"), [
    ("job_type", "job_type", "Contract"), ("work_mode", "work_mode", "Hybrid"),
    ("experience_level", "experience_level", "Senior"),
    ("nationality", "nationality_required", "GCC"),
    ("gender", "gender_required", "Female"),
])
def test_exact_filters_are_trimmed_and_remain_exact(session_factory, parameter, field, value):
    add_jobs(session_factory, {field: value}, {field: value + " extra"})
    assert get(**{parameter: f"  {value}  "})[1]["total"] == 1


def test_boolean_filters_retain_semantics(session_factory):
    add_jobs(session_factory, {"work_mode": "Remote", "arabic_required": "Yes"}, {})
    assert get(remote_only="true")[1]["total"] == 1
    assert get(arabic_only="true")[1]["total"] == 1


def test_percent_and_underscore_are_literal(session_factory):
    add_jobs(session_factory, {"title": "100% Remote"}, {"title": "plain"}, {"title": "data_engineer"})
    assert [row["title"] for row in get(search="%")[1]["results"]] == ["100% Remote"]
    assert [row["title"] for row in get(search="_")[1]["results"]] == ["data_engineer"]


def test_numeric_salary_ordering_and_malformed_values(session_factory):
    add_jobs(
        session_factory,
        {"title": "Nine hundred", "salary_min": "900", "salary_max": "900"},
        {"title": "Ten thousand", "salary_min": "10000", "salary_max": "10000"},
        {"title": "Decimal", "salary_min": "2500.50", "salary_max": "2500.50"},
        {"title": "Empty", "salary_min": "", "salary_max": ""},
        {"title": "Null", "salary_min": None, "salary_max": None},
        {"title": "Malformed", "salary_min": "Negotiable", "salary_max": "Negotiable"},
    )
    high = [row["title"] for row in get(sort="highest_salary")[1]["results"]]
    low = [row["title"] for row in get(sort="lowest_salary")[1]["results"]]
    assert high[:3] == ["Ten thousand", "Decimal", "Nine hundred"]
    assert low[:3] == ["Nine hundred", "Decimal", "Ten thousand"]
    assert set(high[3:]) == set(low[3:]) == {"Empty", "Null", "Malformed"}


def test_min_salary_is_numeric_and_rejects_malformed(session_factory):
    add_jobs(
        session_factory,
        {"title": "900", "salary_max": "900"},
        {"title": "10000", "salary_max": "10000"},
        {"title": "decimal", "salary_max": "2500.50"},
        {"title": "bad", "salary_max": "Negotiable"},
    )
    assert [row["title"] for row in get(min_salary=1000)[1]["results"]] == ["decimal", "10000"]


def test_stable_sort_tie_breakers(session_factory):
    add_jobs(
        session_factory,
        {"date_posted": "2026-01-01", "salary_min": "100", "salary_max": "100"},
        {"date_posted": "2026-01-01", "salary_min": "100", "salary_max": "100"},
        {"date_posted": "2026-01-02", "salary_min": "100", "salary_max": "100"},
    )
    newest = [row["id"] for row in get(sort="newest")[1]["results"]]
    oldest = [row["id"] for row in get(sort="oldest")[1]["results"]]
    highest = [row["id"] for row in get(sort="highest_salary")[1]["results"]]
    assert newest == [3, 2, 1]
    assert oldest == [1, 2, 3]
    assert highest == [3, 2, 1]
    assert [row["id"] for row in get(sort="highest_salary")[1]["results"]] == highest


def test_blank_dates_sort_last(session_factory):
    add_jobs(session_factory, {"date_posted": None}, {"date_posted": ""}, {"date_posted": "2026-01-01"})
    assert get(sort="newest")[1]["results"][0]["id"] == 3
    assert get(sort="oldest")[1]["results"][0]["id"] == 3


def test_date_range_uses_fixed_utc_date_and_excludes_missing(session_factory, monkeypatch):
    monkeypatch.setattr(app_module, "utc_today", lambda: date(2026, 8, 1))
    add_jobs(
        session_factory, {"date_posted": "2026-07-25"}, {"date_posted": "2026-07-20"},
        {"date_posted": ""}, {"date_posted": None},
    )
    assert [row["id"] for row in get(date_range=10)[1]["results"]] == [1]


def test_openapi_exposes_paginated_response_schema(session_factory):
    status, body = get("/openapi.json")
    assert status == 200
    schema = body["paths"]["/jobs"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("/PaginatedJobsResponse")


def test_job_detail_returns_complete_exact_contract(session_factory):
    values = {
        "title": "مهندس برمجيات", "description": "First line\nالسطر الثاني",
        "skills": "Python, العربية", "country": "Qatar", "city": "Doha",
        "area": "West Bay", "company_name": "شركة الاختبار",
        "category": "Engineering", "industry": "Technology",
        "salary_min": "12000", "salary_max": "18000", "salary_currency": "QAR",
        "salary_period": "Monthly", "job_type": "Full Time", "work_mode": "Hybrid",
        "experience_level": "Senior", "nationality_required": "Any",
        "gender_required": "Any", "arabic_required": "Yes",
        "languages_required": "Arabic, English", "date_posted": "2026-08-01",
        "closing_date": "2026-09-01", "apply_url": "https://example.test/apply/مهندس",
        "source": "Test Source",
    }
    add_jobs(session_factory, values)

    status, body = get("/jobs/1")

    assert status == 200
    assert set(body) == RESULT_FIELDS
    assert body == {"id": 1, **values}
    assert body["description"] == "First line\nالسطر الثاني"
    assert body["apply_url"] == values["apply_url"]


def test_job_detail_serializes_nullable_fields_as_null(session_factory):
    nullable = {field: None for field in RESULT_FIELDS - {"id", "apply_url"}}
    add_jobs(session_factory, nullable | {"apply_url": "https://example.test/nullable"})

    status, body = get("/jobs/1")

    assert status == 200
    assert set(body) == RESULT_FIELDS
    assert all(body[field] is None for field in nullable)


def test_missing_job_detail_returns_exact_404(session_factory):
    assert get("/jobs/999999") == (404, {"detail": "Job not found"})


@pytest.mark.parametrize("path", ["/jobs/0", "/jobs/-1", "/jobs/not-a-number"])
def test_job_detail_path_validation_returns_422(session_factory, path):
    assert get(path)[0] == 422


def test_job_detail_is_read_only(session_factory):
    values = {"title": "Unchanged", "description": "Before\nبعد"}
    add_jobs(session_factory, values)
    with session_factory() as session:
        before_count = session.query(Job).count()
        before = {column.name: getattr(session.get(Job, 1), column.name)
                  for column in Job.__table__.columns}

    assert get("/jobs/1")[0] == 200

    with session_factory() as session:
        after_count = session.query(Job).count()
        after = {column.name: getattr(session.get(Job, 1), column.name)
                 for column in Job.__table__.columns}
    assert after_count == before_count == 1
    assert after == before


@pytest.mark.parametrize(("path", "expected_status"), [("/jobs/1", 200), ("/jobs/2", 404)])
def test_job_detail_closes_session(session_factory, monkeypatch, path, expected_status):
    add_jobs(session_factory, {})
    closed = []

    class TrackingSession:
        def __init__(self):
            self.session = session_factory()

        def __enter__(self):
            return self.session

        def __exit__(self, exception_type, exception, traceback):
            self.session.close()
            closed.append(True)

    monkeypatch.setattr(app_module, "Session", TrackingSession)
    assert get(path)[0] == expected_status
    assert closed == [True]


def test_list_route_regression_with_existing_parameters(session_factory):
    add_jobs(session_factory, {"title": "Needle", "country": "UAE"})
    status, body = get("/jobs", search="Needle", location="UAE", sort="newest", page=1, limit=10)
    assert status == 200
    assert set(body) == {
        "page", "limit", "total", "total_pages", "has_next", "has_previous", "results"
    }
    assert set(body["results"][0]) == RESULT_FIELDS
    assert get("/jobs", sort="invalid")[0] == 422


def test_openapi_exposes_job_detail_contract(session_factory):
    status, body = get("/openapi.json")
    operation = body["paths"]["/jobs/{job_id}"]["get"]
    parameter = operation["parameters"][0]
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]

    assert status == 200
    assert parameter["name"] == "job_id" and parameter["in"] == "path"
    assert parameter["required"] is True
    assert parameter["schema"]["type"] == "integer"
    assert parameter["schema"]["minimum"] == 1
    assert response["$ref"].endswith("/JobResult")
