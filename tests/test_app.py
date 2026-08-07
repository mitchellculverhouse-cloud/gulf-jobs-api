import asyncio
import json
import pathlib
from datetime import date
from urllib.parse import urlencode

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app as app_module
import importer
from models import Base, Job


RESULT_FIELDS = {
    "id", "title", "description", "skills", "country", "city", "area",
    "company_name", "category", "industry", "salary_min", "salary_max",
    "salary_currency", "salary_period", "job_type", "work_mode",
    "experience_level", "nationality_required", "gender_required",
    "arabic_required", "languages_required", "date_posted", "closing_date",
    "apply_url", "source",
}

FILTER_OPTION_FIELDS = {
    "countries", "cities", "categories", "industries", "job_types",
    "work_modes", "experience_levels", "currencies", "salary_periods",
    "languages",
}
FIXTURES = pathlib.Path(__file__).parent / "fixtures"


async def _asgi_request(path, method="GET", params=None, headers=None):
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
        "method": method, "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": query_string,
        "headers": [(name.lower().encode(), value.encode())
                    for name, value in (headers or {}).items()],
        "client": ("test", 123), "server": ("test", 80),
        "root_path": "",
    }
    await app_module.app(scope, receive, send)
    status = next(message["status"] for message in messages
                  if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages
                    if message["type"] == "http.response.body")
    return status, json.loads(body)


def get(path="/jobs", **params):
    return asyncio.run(_asgi_request(path, params=params))


def post(path, headers=None, **params):
    return asyncio.run(_asgi_request(path, method="POST", params=params, headers=headers))


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


def test_filter_options_clean_deduplicate_and_sort_values(session_factory):
    add_jobs(
        session_factory,
        {
            "country": " Saudi Arabia ", "city": "Riyadh", "category": "Engineering",
            "industry": "Construction", "job_type": " Full Time ",
            "work_mode": "On-site", "experience_level": "Senior",
            "salary_currency": " SAR ", "salary_period": "Monthly",
            "languages_required": "Arabic, English",
        },
        {
            "country": "Saudi Arabia", "city": " Dubai ", "category": "Technology",
            "industry": "Software", "job_type": "Full Time", "work_mode": " Remote ",
            "experience_level": "Mid", "salary_currency": "SAR",
            "salary_period": " Annual ", "languages_required": "English",
        },
        {
            "country": None, "city": "", "category": "   ", "industry": None,
            "job_type": "\t", "work_mode": "", "experience_level": None,
            "salary_currency": "   ", "salary_period": None,
            "languages_required": "  ",
        },
    )

    status, body = get("/jobs/filter-options")

    assert status == 200
    assert set(body) == FILTER_OPTION_FIELDS
    assert all(isinstance(body[field], list) for field in FILTER_OPTION_FIELDS)
    assert body == {
        "countries": ["Saudi Arabia"],
        "cities": ["Dubai", "Riyadh"],
        "categories": ["Engineering", "Technology"],
        "industries": ["Construction", "Software"],
        "job_types": ["Full Time"],
        "work_modes": ["On-site", "Remote"],
        "experience_levels": ["Mid", "Senior"],
        "currencies": ["SAR"],
        "salary_periods": ["Annual", "Monthly"],
        "languages": ["Arabic, English", "English"],
    }


def test_multi_value_text_filter_options_are_individual_deduplicated_and_usable(session_factory):
    add_jobs(
        session_factory,
        {"category": "Management, Engineering", "industry": "Construction, Technology"},
        {"category": "Engineering", "industry": "Technology"},
        {"category": "Human Resources", "industry": "Business Services"},
    )

    status, options = get("/jobs/filter-options")

    assert status == 200
    assert options["categories"] == ["Engineering", "Human Resources", "Management"]
    assert options["industries"] == ["Business Services", "Construction", "Technology"]
    for field, query_name in (("categories", "category"), ("industries", "industry")):
        for option in options[field]:
            result_status, result = get(**{query_name: option})
            assert result_status == 200
            assert result["total"] >= 1


def test_exact_filter_options_keep_compound_values_submittable(session_factory):
    add_jobs(session_factory, {
        "job_type": "Full Time, Part Time", "work_mode": "Hybrid",
        "experience_level": "Entry, Mid",
    })

    status, options = get("/jobs/filter-options")

    assert status == 200
    assert options["job_types"] == ["Full Time, Part Time"]
    assert options["work_modes"] == ["Hybrid"]
    assert options["experience_levels"] == ["Entry, Mid"]
    for name, value in (
        ("job_type", options["job_types"][0]),
        ("work_mode", options["work_modes"][0]),
        ("experience_level", options["experience_levels"][0]),
    ):
        assert get(**{name: value})[1]["total"] == 1


def test_structured_import_repairs_legacy_taxonomy_and_filter_options(session_factory):
    url = "https://wuzzuf.net/saudi/jobs/p/structured-sources-engineer"
    malformed_category = (
        "OtherInstallation/Maintenance/RepairInstallation/Maintenance/Repair"
    )
    add_jobs(session_factory, {
        "apply_url": url, "source": "WUZZUF", "category": malformed_category,
        "industry": "ConstructionTechnologyBusiness Services",
        "job_type": "Part-timeFull-timeFull-time, Independent Project",
        "work_mode": "Remote WorkWork from Office Company",
    })
    values = importer.parse_wuzzuf_detail(
        (FIXTURES / "wuzzuf_mixed_sources_detail.html").read_text(),
        url,
        importer.SOURCES[0],
    )

    with session_factory() as session:
        original = session.query(Job).one()
        original_id = original.id
        assert importer.save_job(session, values) == ("updated", 0)
        repaired = session.get(Job, original_id)
        assert repaired.apply_url == url
        assert repaired.category == "Installation/Maintenance/Repair, Other"
        assert repaired.industry == "Construction, Business Services"
        assert repaired.job_type == "Full Time, Part Time"
        assert repaired.work_mode == "Hybrid"
        assert importer.save_job(session, values) == ("unchanged", 0)
        assert session.query(Job).count() == 1

    status, options = get("/jobs/filter-options")
    assert status == 200
    assert options["categories"] == []
    assert options["industries"] == []
    assert options["job_types"] == ["Full Time, Part Time"]
    assert options["work_modes"] == ["Hybrid"]
    assert malformed_category not in options["categories"]
    for field, query_name in (
        ("categories", "category"), ("job_types", "job_type"),
        ("work_modes", "work_mode"),
    ):
        for option in options[field]:
            result_status, result = get(**{query_name: option})
            assert result_status == 200
            assert result["total"] == 1


def test_filter_options_suppress_untrustworthy_wuzzuf_legacy_values(session_factory):
    add_jobs(session_factory, {
        "source": "WUZZUF", "category": "OtherInstallationMaintenanceRepair",
        "industry": "ConstructionTechnologyBusiness Services",
        "job_type": "Part-timeFull-time", "work_mode": "Remote WorkWork from Office",
    }, {
        "source": "WUZZUF", "category": "Engineering, Software Development",
        "industry": "Still Untrusted", "job_type": "Full Time", "work_mode": "Remote",
    }, {
        "source": "Employer", "category": "Sales", "industry": "Retail",
        "job_type": "Flexible", "work_mode": "Field-based",
    })

    status, options = get("/jobs/filter-options")

    assert status == 200
    assert options["categories"] == ["Sales"]
    assert options["industries"] == ["Retail"]
    assert options["job_types"] == ["Flexible", "Full Time"]
    assert options["work_modes"] == ["Field-based", "Remote"]
    for option_name, query_name in (
        ("categories", "category"), ("job_types", "job_type"),
        ("work_modes", "work_mode"),
    ):
        for option in options[option_name]:
            assert get(**{query_name: option})[1]["total"] >= 1


def test_filter_options_strictly_validate_wuzzuf_taxonomy_and_experience(session_factory):
    add_jobs(session_factory, {
        "source": "WUZZUF", "category": "AdministrationSales/Retail",
        "job_type": "Full TimePart Time", "work_mode": "RemoteHybrid",
        "experience_level": "1+ years2 - 3 years10 - 15 years",
    }, {
        "source": "WUZZUF", "category": "Administration, Analyst/Research",
        "job_type": "Freelance / Project", "work_mode": "On-site",
        "experience_level": "1+ years",
    }, {
        "source": "WUZZUF", "category": None, "job_type": None, "work_mode": None,
        "experience_level": "4+ years",
    }, {
        "source": "WUZZUF", "category": None, "job_type": None, "work_mode": None,
        "experience_level": "2 - 3 years",
    }, {
        "source": "WUZZUF", "category": None, "job_type": None, "work_mode": None,
        "experience_level": "10 - 15 years",
    }, {
        "source": "WUZZUF", "category": None, "job_type": None, "work_mode": None,
        "experience_level": "Up to 2 years",
    }, {
        "source": "Employer", "category": None, "job_type": None, "work_mode": None,
        "experience_level": "Future source format",
    })

    status, options = get("/jobs/filter-options")
    assert status == 200
    assert options["categories"] == ["Administration", "Analyst/Research"]
    assert options["job_types"] == ["Freelance / Project"]
    assert options["work_modes"] == ["On-site"]
    assert options["experience_levels"] == [
        "1+ years", "10 - 15 years", "2 - 3 years", "4+ years",
        "Future source format", "Up to 2 years",
    ]
    for option_name, query_name in (
        ("categories", "category"), ("job_types", "job_type"),
        ("work_modes", "work_mode"), ("experience_levels", "experience_level"),
    ):
        for option in options[option_name]:
            assert get(**{query_name: option})[1]["total"] >= 1


def test_filter_options_empty_database_returns_stable_empty_contract(session_factory):
    status, body = get("/jobs/filter-options")

    assert status == 200
    assert body == {field: [] for field in FILTER_OPTION_FIELDS}


def test_filter_options_static_route_is_not_job_id_validation(session_factory):
    status, body = get("/jobs/filter-options")

    assert status == 200
    assert set(body) == FILTER_OPTION_FIELDS
    assert "detail" not in body


def test_filter_options_is_read_only(session_factory):
    add_jobs(session_factory, {"country": " UAE ", "languages_required": " English "})

    assert get("/jobs/filter-options")[0] == 200

    with session_factory() as session:
        job = session.get(Job, 1)
        assert job.country == " UAE "
        assert job.languages_required == " English "


def test_openapi_exposes_filter_options_response_schema(session_factory):
    status, body = get("/openapi.json")
    schema = body["paths"]["/jobs/filter-options"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]

    assert status == 200
    assert schema["$ref"].endswith("/JobFilterOptionsResponse")


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


def test_health_executes_select_one_and_closes_session(session_factory, monkeypatch):
    events = []

    class TrackingSession:
        def __init__(self):
            self.session = session_factory()

        def execute(self, statement):
            events.append(str(statement))
            return self.session.execute(statement)

        def close(self):
            self.session.close()
            events.append("closed")

    monkeypatch.setattr(app_module, "Session", TrackingSession)

    assert get("/health") == (200, {"status": "ok"})
    assert events == ["SELECT 1", "closed"]


def test_health_failure_is_generic_and_closes_session(monkeypatch):
    events = []

    class FailingSession:
        def execute(self, statement):
            events.append(str(statement))
            raise RuntimeError("secret database hostname")

        def close(self):
            events.append("closed")

    monkeypatch.setattr(app_module, "Session", FailingSession)

    assert get("/health") == (503, {"detail": "Database unavailable"})
    assert events == ["SELECT 1", "closed"]


def test_import_requires_server_configuration(monkeypatch):
    monkeypatch.delenv("IMPORT_API_KEY", raising=False)
    assert post("/run-import") == (
        503, {"detail": "Import endpoint is not configured"}
    )


@pytest.mark.parametrize("headers", [None, {"X-Import-Key": ""}, {"X-Import-Key": "wrong"}])
def test_import_rejects_missing_empty_or_incorrect_key(monkeypatch, headers):
    monkeypatch.setenv("IMPORT_API_KEY", "correct-key")
    assert post("/run-import", headers=headers) == (401, {"detail": "Invalid import key"})


def test_authenticated_import_runs_once_and_returns_success(monkeypatch):
    calls = []
    monkeypatch.setenv("IMPORT_API_KEY", "correct-key")
    summary = {"listing_links_found": 4, "unique_job_urls": 4, "inserted": 1,
               "updated": 2, "unchanged": 1, "failed_detail_pages": 0,
               "duplicate_database_urls": 0}
    monkeypatch.setattr(app_module, "run_import", lambda: calls.append(True) or summary)

    assert post("/run-import", headers={"X-Import-Key": "correct-key"}) == (
        200, {"status": "completed", **summary}
    )
    assert calls == [True]


def test_import_lock_is_released_after_success(monkeypatch):
    calls = []
    monkeypatch.setenv("IMPORT_API_KEY", "correct-key")
    monkeypatch.setattr(app_module, "run_import", lambda: calls.append(True))
    headers = {"X-Import-Key": "correct-key"}

    assert post("/run-import", headers=headers)[0] == 200
    assert post("/run-import", headers=headers)[0] == 200
    assert calls == [True, True]


def test_get_import_is_405_and_openapi_only_documents_post(monkeypatch):
    calls = []
    monkeypatch.setenv("IMPORT_API_KEY", "correct-key")
    monkeypatch.setattr(app_module, "run_import", lambda: calls.append(True))

    assert get("/run-import")[0] == 405
    operations = get("/openapi.json")[1]["paths"]["/run-import"]
    assert "post" in operations and "get" not in operations
    assert calls == []


def test_import_key_is_not_accepted_as_query_parameter(monkeypatch):
    monkeypatch.setenv("IMPORT_API_KEY", "correct-key")
    assert post("/run-import", x_import_key="correct-key") == (
        401, {"detail": "Invalid import key"}
    )


def test_import_failure_is_generic_and_releases_lock(monkeypatch):
    calls = []
    monkeypatch.setenv("IMPORT_API_KEY", "correct-key")

    def fail_then_succeed():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("private importer failure details")

    monkeypatch.setattr(app_module, "run_import", fail_then_succeed)
    headers = {"X-Import-Key": "correct-key"}

    status, body = post("/run-import", headers=headers)
    assert (status, body) == (500, {"detail": "Import failed"})
    assert "private importer failure details" not in json.dumps(body)
    assert post("/run-import", headers=headers) == (200, {"status": "completed"})
    assert calls == [True, True]


def test_overlapping_import_returns_conflict_without_running(monkeypatch):
    calls = []
    monkeypatch.setenv("IMPORT_API_KEY", "correct-key")
    monkeypatch.setattr(app_module, "run_import", lambda: calls.append(True))
    app_module.IMPORT_LOCK.acquire()
    try:
        assert post("/run-import", headers={"X-Import-Key": "correct-key"}) == (
            409, {"detail": "Import already running"}
        )
    finally:
        app_module.IMPORT_LOCK.release()
    assert calls == []


@pytest.mark.parametrize("headers", [None, {"X-Import-Key": "wrong"}])
def test_wuzzuf_backfill_rejects_missing_or_wrong_key(monkeypatch, headers):
    monkeypatch.setenv("IMPORT_API_KEY", "correct-key")
    assert post("/maintenance/backfill-wuzzuf-filters", headers=headers) == (
        401, {"detail": "Invalid import key"})


def test_authenticated_wuzzuf_backfill_returns_summary(monkeypatch):
    summary = {"scanned": 3, "updated": 1, "unchanged": 1,
               "skipped_missing_page": 1,
               "skipped_no_authoritative_data": 0, "failed": 0,
               "failure_diagnostics": {
                   "http_403": 0, "http_429": 0, "http_5xx": 0,
                   "http_other": 0, "timeout": 0, "connection_error": 0,
                   "parser_or_unexpected": 0, "database": 0,
               }}
    monkeypatch.setenv("IMPORT_API_KEY", "correct-key")
    monkeypatch.setattr(app_module, "backfill_wuzzuf_filters", lambda: summary)

    assert post("/maintenance/backfill-wuzzuf-filters",
                headers={"X-Import-Key": "correct-key"}) == (
        200, {"status": "completed", **summary})


def test_wuzzuf_backfill_is_post_only_and_shares_import_lock(monkeypatch):
    calls = []
    monkeypatch.setenv("IMPORT_API_KEY", "correct-key")
    monkeypatch.setattr(app_module, "backfill_wuzzuf_filters", lambda: calls.append(True))
    assert get("/maintenance/backfill-wuzzuf-filters")[0] == 405
    operations = get("/openapi.json")[1]["paths"]["/maintenance/backfill-wuzzuf-filters"]
    assert "post" in operations and "get" not in operations
    app_module.IMPORT_LOCK.acquire()
    try:
        assert post("/maintenance/backfill-wuzzuf-filters",
                    headers={"X-Import-Key": "correct-key"}) == (
            409, {"detail": "Import already running"})
    finally:
        app_module.IMPORT_LOCK.release()
    assert calls == []


class ConnectivityResponse:
    def __init__(self, status_code, url, content_type="text/html", headers=None):
        self.status_code = status_code
        self.url = url
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.closed = False

    @property
    def content(self):
        raise AssertionError("connectivity check must never access the response body")

    def close(self):
        self.closed = True


class ConnectivitySession:
    def __init__(self, outcomes, calls):
        self.outcomes = iter(outcomes)
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def configure_connectivity(monkeypatch, outcomes, sources=None):
    calls = []
    configured = sources or [
        {"name": "Source One", "url": "https://source-one.example/jobs",
         "timeout": 12, "active": False},
    ]
    monkeypatch.setenv("IMPORT_API_KEY", "correct-key")
    monkeypatch.setattr(app_module, "SOURCES", configured)
    monkeypatch.setattr(
        app_module.requests, "Session", lambda: ConnectivitySession(outcomes, calls))
    return calls


@pytest.mark.parametrize("headers", [None, {"X-Import-Key": ""},
                                      {"X-Import-Key": "wrong"}])
def test_source_check_rejects_missing_empty_or_wrong_key(monkeypatch, headers):
    monkeypatch.setenv("IMPORT_API_KEY", "correct-key")
    monkeypatch.setattr(app_module.requests, "Session",
                        lambda: pytest.fail("network must not be used"))
    assert post("/maintenance/check-sources", headers=headers) == (
        401, {"detail": "Invalid import key"})


def test_source_check_requires_configured_key(monkeypatch):
    monkeypatch.delenv("IMPORT_API_KEY", raising=False)
    assert post("/maintenance/check-sources") == (
        503, {"detail": "Import endpoint is not configured"})


def test_source_check_is_post_only(monkeypatch):
    monkeypatch.setenv("IMPORT_API_KEY", "correct-key")
    assert get("/maintenance/check-sources")[0] == 405
    operations = get("/openapi.json")[1]["paths"]["/maintenance/check-sources"]
    assert "post" in operations and "get" not in operations


def test_source_check_streams_direct_200_without_reading_body(monkeypatch):
    response = ConnectivityResponse(
        200, "https://source-one.example/jobs",
        content_type="text/html; charset=utf-8",
        headers={"Content-Length": "5"},
    )
    calls = configure_connectivity(monkeypatch, [response])
    status, body = post("/maintenance/check-sources",
                        headers={"X-Import-Key": "correct-key"})
    assert status == 200
    assert body == {"sources": [{
        "source": "Source One",
        "configured_url": "https://source-one.example/jobs",
        "http_status": 200,
        "content_type": "text/html; charset=utf-8",
        "response_size_bytes": 5,
        "redirected": False,
        "final_host": "source-one.example",
        "reachable": True,
        "error_category": None,
    }]}
    assert calls == [("https://source-one.example/jobs", {
        "headers": app_module.HEADERS,
        "timeout": 12,
        "allow_redirects": False,
        "stream": True,
    })]
    assert response.closed is True


@pytest.mark.parametrize("content_length", [None, "", "unknown", "-1", "1.5"])
def test_source_check_reports_null_for_missing_or_invalid_content_length(
    monkeypatch, content_length,
):
    headers = {} if content_length is None else {"Content-Length": content_length}
    response = ConnectivityResponse(
        200, "https://source-one.example/jobs", headers=headers)
    configure_connectivity(monkeypatch, [response])
    _, body = post("/maintenance/check-sources",
                   headers={"X-Import-Key": "correct-key"})
    assert body["sources"][0]["response_size_bytes"] is None
    assert response.closed is True


def test_source_check_reports_redirect_without_following_target(monkeypatch):
    response = ConnectivityResponse(
        302,
        "https://source-one.example/jobs",
        headers={"Location": "https://www.example.com/jobs", "Content-Length": "0"},
    )
    calls = configure_connectivity(monkeypatch, [response])
    status, body = post("/maintenance/check-sources",
                        headers={"X-Import-Key": "correct-key"})
    result = body["sources"][0]
    assert status == 200
    assert result["http_status"] == 302
    assert result["redirected"] is True
    assert result["final_host"] == "www.example.com"
    assert result["reachable"] is False
    assert result["response_size_bytes"] == 0
    assert len(calls) == 1
    assert calls[0][0] == "https://source-one.example/jobs"
    assert response.closed is True


def test_source_check_never_fetches_malicious_redirect_target(monkeypatch):
    target = "http://169.254.169.254/latest/meta-data"
    response = ConnectivityResponse(
        302, "https://source-one.example/jobs", headers={"Location": target})
    calls = configure_connectivity(monkeypatch, [response])
    _, body = post("/maintenance/check-sources",
                   headers={"X-Import-Key": "correct-key"})
    assert body["sources"][0]["final_host"] == "169.254.169.254"
    assert [url for url, _ in calls] == ["https://source-one.example/jobs"]
    assert target not in [url for url, _ in calls]


@pytest.mark.parametrize("status_code", [403, 429, 500])
def test_source_check_preserves_distinguishable_http_failure(monkeypatch, status_code):
    response = ConnectivityResponse(status_code, "https://source-one.example/jobs")
    configure_connectivity(monkeypatch, [response])
    status, body = post("/maintenance/check-sources",
                        headers={"X-Import-Key": "correct-key"})
    result = body["sources"][0]
    assert status == 200
    assert result["http_status"] == status_code
    assert result["reachable"] is False
    assert result["error_category"] is None
    assert response.closed is True


@pytest.mark.parametrize("error, category", [
    (importer.requests.Timeout("private timeout detail"), "timeout"),
    (importer.requests.ConnectionError("private connection detail"), "connection_error"),
])
def test_source_check_safely_classifies_network_errors(monkeypatch, error, category):
    configure_connectivity(monkeypatch, [error])
    status, body = post("/maintenance/check-sources",
                        headers={"X-Import-Key": "correct-key"})
    result = body["sources"][0]
    assert status == 200
    assert result["error_category"] == category
    assert result["http_status"] is None
    assert result["reachable"] is False
    assert "private" not in json.dumps(body)


def test_source_failure_does_not_stop_later_configured_source(monkeypatch):
    sources = [
        {"name": "First", "url": "https://first.example/jobs", "timeout": 3},
        {"name": "Second", "url": "https://second.example/jobs", "timeout": 4},
    ]
    response = ConnectivityResponse(200, "https://second.example/jobs")
    calls = configure_connectivity(monkeypatch, [
        importer.requests.ConnectionError("offline"), response,
    ], sources)
    status, body = post("/maintenance/check-sources",
                        headers={"X-Import-Key": "correct-key"})
    assert status == 200
    assert [result["error_category"] for result in body["sources"]] == [
        "connection_error", None]
    assert [result["reachable"] for result in body["sources"]] == [False, True]
    assert [url for url, _ in calls] == [source["url"] for source in sources]
    assert response.closed is True


def test_redirect_makes_exactly_one_request_per_configured_source(monkeypatch):
    sources = [
        {"name": "First", "url": "https://first.example/jobs"},
        {"name": "Second", "url": "https://second.example/jobs"},
    ]
    responses = [
        ConnectivityResponse(302, source["url"], headers={"Location": "/canonical"})
        for source in sources
    ]
    calls = configure_connectivity(monkeypatch, responses, sources)
    _, body = post("/maintenance/check-sources",
                   headers={"X-Import-Key": "correct-key"})
    assert [url for url, _ in calls] == [source["url"] for source in sources]
    assert all(result["redirected"] for result in body["sources"])
    assert all(not result["reachable"] for result in body["sources"])
    assert all(response.closed for response in responses)


def test_source_check_ignores_caller_url_and_performs_no_database_work(monkeypatch):
    response = ConnectivityResponse(200, "https://source-one.example/jobs")
    calls = configure_connectivity(monkeypatch, [response])
    monkeypatch.setattr(app_module, "Session",
                        lambda: pytest.fail("database must not be accessed"))
    status, body = post(
        "/maintenance/check-sources",
        headers={"X-Import-Key": "correct-key"},
        url="http://169.254.169.254/latest/meta-data",
    )
    assert status == 200
    assert [url for url, _ in calls] == ["https://source-one.example/jobs"]
    assert body["sources"][0]["configured_url"] == "https://source-one.example/jobs"


def test_source_check_does_not_change_existing_import_behavior(monkeypatch):
    calls = []
    monkeypatch.setenv("IMPORT_API_KEY", "correct-key")
    monkeypatch.setattr(app_module, "run_import", lambda: calls.append(True))
    assert post("/run-import", headers={"X-Import-Key": "correct-key"}) == (
        200, {"status": "completed"})
    assert calls == [True]
