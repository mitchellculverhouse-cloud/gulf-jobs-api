import pathlib

import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import importer
from models import Base, Job


FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text()


class Response:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(str(self.status_code))
            error.response = self
            raise error


class FakeHTTP:
    def __init__(self, responses):
        self.responses = {key: list(value) for key, value in responses.items()}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses[url].pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def source(**changes):
    value = dict(importer.SOURCES[0])
    value.update({"polite_delay": 0, "max_retries": 0}, **changes)
    return value


def test_listing_extracts_only_jobs_and_deduplicates():
    html = fixture("wuzzuf_listing.html")
    jobs = importer.parse_wuzzuf_listing(html, "https://wuzzuf.net/saudi/a/jobs-in-saudi-arabia")
    assert [job["link"] for job in jobs] == [
        "https://wuzzuf.net/jobs/p/alpha-software-engineer",
        "https://wuzzuf.net/jobs/p/beta-accountant",
    ]
    assert importer.count_wuzzuf_listing_links(html, "https://wuzzuf.net") == 3


def test_listing_classifications_are_structured_and_associated_by_job_url():
    jobs = importer.parse_wuzzuf_listing(
        fixture("wuzzuf_listing.html"), "https://wuzzuf.net/saudi/a/jobs-in-saudi-arabia")

    assert jobs[0] == {
        "title": "Software Engineer",
        "link": "https://wuzzuf.net/jobs/p/alpha-software-engineer",
        "category": "Software Development, Engineering",
        "job_type": "Full Time", "work_mode": "Remote",
        "_authoritative_fields": ("category", "job_type", "work_mode"),
    }
    assert jobs[1]["category"] == "Accounting/Finance"
    assert jobs[1]["job_type"] == "Part Time"
    assert jobs[1]["work_mode"] == "On-site"
    assert "Python" not in jobs[0]["category"] and "SQL" not in jobs[0]["category"]


def test_jsonld_extracts_enriched_company_location_salary_and_dates():
    job = importer.parse_wuzzuf_detail(fixture("wuzzuf_jsonld_detail.html"), "https://wuzzuf.net/jobs/p/alpha", source())
    assert job["company_name"] == "Gulf Technology Co."
    assert job["country"] == "Saudi Arabia"
    assert (job["city"], job["area"]) == ("Riyadh", "Al Olaya")
    assert (job["salary_min"], job["salary_max"], job["salary_currency"], job["salary_period"]) == ("12000", "18000", "SAR", "Monthly")
    assert job["job_type"] == "Full Time"
    assert job["date_posted"] == "2026-07-01"
    assert job["closing_date"] == "2026-08-15"
    assert job["description"] == "Build reliable services."


def test_embedded_structured_data_fallback():
    job = importer.parse_wuzzuf_detail(fixture("wuzzuf_embedded_detail.html"), "https://wuzzuf.net/jobs/p/embedded", source())
    assert job["title"] == "Embedded Data Analyst"
    assert job["company_name"] == "Insights Arabia"
    assert job["job_type"] == "Contract"
    assert job["city"] == "Jeddah"


def test_semantic_html_fallback_and_missing_values():
    job = importer.parse_wuzzuf_detail(fixture("wuzzuf_html_detail.html"), "https://wuzzuf.net/jobs/p/html", source())
    assert job["company_name"] == "Red Sea Logistics"
    assert job["job_type"] == "Full Time"
    assert job["experience_level"] == "5 years"
    assert job["languages_required"] == "Arabic, English"
    assert job["salary_min"] == ""
    assert job["closing_date"] is None
    assert job["country"] == "Saudi Arabia"


def test_current_live_wuzzuf_embedded_state_extracts_detail_fields():
    job = importer.parse_wuzzuf_detail(
        fixture("wuzzuf_live_english_detail.html"),
        "https://wuzzuf.net/saudi/jobs/p/live-office-coordinator-riyadh-saudi-arabia",
        source(),
    )
    assert (job["company_name"], job["city"], job["area"], job["country"]) == (
        "Gulf Services", "Riyadh", "Al Olaya", "Saudi Arabia")
    assert "Coordinate daily office operations." in job["description"]
    assert "Job Requirements\n- Clear written communication." in job["description"]
    assert (job["job_type"], job["work_mode"]) == ("Full Time", "On-site")
    assert job["skills"] == "Microsoft Office, Scheduling"
    assert job["category"] == "Administration"
    assert job["industry"] == "Business Services"
    assert job["experience_level"] == "2 - 4 years"
    assert (job["date_posted"], job["closing_date"]) == ("2026-07-31", "2026-09-29")
    assert (job["salary_min"], job["salary_max"], job["salary_currency"], job["salary_period"]) == (
        "6000", "8000", "SAR", "Monthly")
    assert importer.validate_wuzzuf_detail(job) is True


def test_wuzzuf_structured_multi_values_are_preserved_normalized_and_deduplicated():
    html = fixture("wuzzuf_live_english_detail.html").replace(
        '"workRoles":[{"name":"Administration"}]',
        '"workRoles":[{"name":"Management"},{"name":"Engineering"},{"name":"Management"}]',
    ).replace(
        '"workTypes":[{"displayedName":"Full Time"}]',
        '"workTypes":[{"displayedName":"Full-time"},{"displayedName":"Part_time"},{"displayedName":"Full Time"}]',
    ).replace(
        '"workIndustries":[{"name":"Business Services"}]',
        '"workIndustries":[{"name":"Construction"},{"name":"Technology"},{"name":"Construction"}]',
    )

    job = importer.parse_wuzzuf_detail(
        html,
        "https://wuzzuf.net/saudi/jobs/p/live-office-coordinator-riyadh-saudi-arabia",
        source(),
    )

    assert job["category"] == "Management, Engineering"
    assert job["industry"] == "Construction, Technology"
    assert job["job_type"] == "Full Time, Part Time"
    assert job["_authoritative_fields"] == (
        "category", "industry", "job_type", "work_mode")


def test_mixed_jsonld_and_store_prefers_structured_classification_fields():
    job = importer.parse_wuzzuf_detail(
        fixture("wuzzuf_mixed_sources_detail.html"),
        "https://wuzzuf.net/saudi/jobs/p/structured-sources-engineer",
        source(),
    )

    assert job["description"] == "Preserve the reliable JSON-LD description."
    assert job["category"] == "Installation/Maintenance/Repair, Other"
    assert job["industry"] == "Construction, Business Services"
    assert job["job_type"] == "Full Time, Part Time"
    assert job["work_mode"] == "Hybrid"
    assert job["_authoritative_fields"] == (
        "category", "industry", "job_type", "work_mode")


def test_jsonld_classification_remains_fallback_when_store_field_is_missing():
    html = fixture("wuzzuf_mixed_sources_detail.html").replace(
        '"workTypes":[{"displayedName":"Full-time"},{"displayedName":"Part_time"}],',
        "",
    )

    job = importer.parse_wuzzuf_detail(
        html,
        "https://wuzzuf.net/saudi/jobs/p/structured-sources-engineer",
        source(),
    )

    assert job["job_type"] == "Part-timeFull-timeFull-time, Independent Project"
    assert "job_type" not in job["_authoritative_fields"]


def test_non_authoritative_fallback_does_not_replace_existing_classification(db_factory):
    url = "https://wuzzuf.net/saudi/jobs/p/structured-sources-engineer"
    html = fixture("wuzzuf_mixed_sources_detail.html").replace(
        '"workTypes":[{"displayedName":"Full-time"},{"displayedName":"Part_time"}],',
        "",
    )
    values = importer.parse_wuzzuf_detail(html, url, source())
    db = db_factory()
    db.add(Job(title="Engineer", apply_url=url, source="WUZZUF", job_type="Contract"))
    db.commit()

    importer.save_job(db, values)

    assert db.query(Job).one().job_type == "Contract"
    db.close()


def test_arabic_url_selects_embedded_entity_without_locale_prefix():
    job = importer.parse_wuzzuf_detail(
        fixture("wuzzuf_live_english_detail.html"),
        "https://www.wuzzuf.net/ar/saudi/jobs/p/live-office-coordinator-riyadh-saudi-arabia/?ref=listing",
        source(),
    )
    assert job["company_name"] == "Gulf Services"
    assert job["description"].startswith("Coordinate daily office operations.")
    assert job["skills"] == "Microsoft Office, Scheduling"
    assert job["category"] == "Administration"
    assert job["salary_min"] == "6000"
    assert job["date_posted"] == "2026-07-31"


@pytest.mark.parametrize("requested, embedded", [
    ("https://wuzzuf.net/ar/saudi/jobs/p/example/?x=1", "saudi/jobs/p/example"),
    ("https://www.wuzzuf.net/en/saudi/jobs/p/example", "/saudi/jobs/p/example/?source=state"),
])
def test_wuzzuf_entity_path_normalizes_locale_slashes_and_query(requested, embedded):
    assert importer._wuzzuf_entity_path(requested) == importer._wuzzuf_entity_path(embedded)


def test_current_live_arabic_semantic_html_structure():
    job = importer.parse_wuzzuf_detail(
        fixture("wuzzuf_live_arabic_detail.html"),
        "https://wuzzuf.net/saudi/jobs/p/arabic-administrator",
        source(),
    )
    assert (job["title"], job["company_name"]) == ("منسق إداري", "شركة سرية")
    assert (job["city"], job["country"]) == ("جدة", "Saudi Arabia")
    assert (job["job_type"], job["work_mode"]) == ("دوام كامل", "عن بعد")
    assert "تنسيق الأعمال الإدارية اليومية." in job["description"]
    assert "مهارات تواصل جيدة." in job["description"]
    assert importer.validate_wuzzuf_detail(job) is True


def test_title_only_detail_is_not_meaningful():
    job = importer.parse_wuzzuf_detail(
        "<h1>Listing title only</h1>", "https://wuzzuf.net/jobs/p/title-only", source())
    assert importer.validate_wuzzuf_detail(job) is False


@pytest.mark.parametrize("description", ["-", "—", "... !!!", "N/A", "NA", "Not specified", "  "])
def test_placeholder_description_is_not_meaningful(description):
    assert importer.validate_wuzzuf_detail({
        "description": description, "company_name": "", "city": "Jeddah",
        "job_type": "Full Time", "work_mode": "On-site",
    }) is False


def test_placeholder_description_and_requirements_do_not_create_heading_content():
    html = '''
    <h1>Secretary</h1>
    <h2>Job Description</h2><div>-</div>
    <h2>Job Requirements</h2><div>—</div>
    <div>Jeddah, Saudi Arabia</div>
    '''
    job = importer.parse_wuzzuf_detail(
        html, "https://wuzzuf.net/ar/saudi/jobs/p/placeholder", source())
    assert job["description"] == ""
    assert importer.validate_wuzzuf_detail(job) is False


@pytest.mark.parametrize("description", [
    "تنسيق الأعمال الإدارية اليومية.",
    "Coordinate daily administrative work.",
])
def test_readable_description_is_meaningful(description):
    assert importer.validate_wuzzuf_detail({"description": description, "city": "Jeddah"}) is True


@pytest.mark.parametrize("company_name", ["-", "—", "...", "N/A", "Not specified"])
def test_placeholder_employer_is_not_meaningful(company_name):
    assert importer.validate_wuzzuf_detail({"company_name": company_name, "city": "Jeddah"}) is False


def test_existing_record_is_enriched_without_overwriting_useful_values(db_factory):
    db = db_factory()
    db.add(Job(title="Original title", apply_url="https://wuzzuf.net/jobs/p/alpha", description="Keep me", company_name=""))
    db.commit()
    outcome, duplicates = importer.save_job(db, {"title": "Scraped title", "apply_url": "https://wuzzuf.net/jobs/p/alpha", "description": "Replacement", "company_name": "Actual Employer", "source": "WUZZUF"})
    saved = db.query(Job).one()
    assert (outcome, duplicates) == ("updated", 0)
    assert saved.id == 1
    assert saved.title == "Original title"
    assert saved.description == "Keep me"
    assert saved.company_name == "Actual Employer"
    db.close()


def test_live_detail_enriches_incomplete_existing_row_without_duplicate(db_factory):
    db = db_factory()
    url = "https://wuzzuf.net/saudi/jobs/p/live-office-coordinator-riyadh-saudi-arabia"
    db.add(Job(title="Office Coordinator", apply_url=url, source="WUZZUF"))
    db.commit()
    original_id = db.query(Job).one().id
    values = importer.parse_wuzzuf_detail(
        fixture("wuzzuf_live_english_detail.html"), url, source())
    assert importer.save_job(db, values)[0] == "updated"
    saved = db.query(Job).one()
    assert saved.id == original_id
    assert saved.company_name == "Gulf Services"
    assert saved.description.startswith("Coordinate daily office operations.")
    assert db.query(Job).count() == 1
    db.close()


def test_arabic_detail_replaces_placeholder_in_incomplete_existing_row(db_factory):
    db = db_factory()
    url = "https://wuzzuf.net/ar/saudi/jobs/p/live-office-coordinator-riyadh-saudi-arabia"
    db.add(Job(
        title="Office Coordinator", apply_url=url, source="WUZZUF",
        country="Saudi Arabia", city="Riyadh", job_type="Full Time", work_mode="On-site",
        description="-", company_name="",
    ))
    db.commit()
    original_id = db.query(Job).one().id
    values = importer.parse_wuzzuf_detail(
        fixture("wuzzuf_live_english_detail.html"), url, source())
    assert importer.validate_wuzzuf_detail(values) is True
    assert importer.save_job(db, values)[0] == "updated"
    saved = db.query(Job).one()
    assert saved.id == original_id
    assert saved.description.startswith("Coordinate daily office operations.")
    assert saved.company_name == "Gulf Services"
    assert db.query(Job).count() == 1
    db.close()


def test_historical_wuzzuf_company_placeholder_is_replaced(db_factory):
    db = db_factory()
    db.add(Job(title="Protected title", apply_url="https://wuzzuf.net/jobs/p/alpha",
               description="Protected description", company_name="  WuZzUf  "))
    db.commit()
    original_id = db.query(Job).one().id
    outcome, _ = importer.save_job(db, {
        "title": "Different title", "apply_url": "https://wuzzuf.net/jobs/p/alpha",
        "description": "Different description", "company_name": "Actual Employer",
        "source": "WUZZUF",
    })
    saved = db.query(Job).one()
    assert outcome == "updated"
    assert saved.id == original_id
    assert saved.company_name == "Actual Employer"
    assert saved.title == "Protected title"
    assert saved.description == "Protected description"
    db.close()


def test_repeated_save_prevents_duplicate_and_returns_zero_duplicate_count(db_factory):
    db = db_factory()
    values = {"title": "Engineer", "apply_url": "https://wuzzuf.net/jobs/p/alpha", "source": "WUZZUF"}
    assert importer.save_job(db, values) == ("inserted", 0)
    assert importer.save_job(db, values) == ("unchanged", 0)
    assert db.query(Job).count() == 1
    db.close()


def test_repeated_save_of_structured_multi_values_is_idempotent(db_factory):
    db = db_factory()
    values = {
        "title": "Engineer", "apply_url": "https://wuzzuf.net/jobs/p/multi",
        "source": "WUZZUF", "category": "Management, Engineering",
        "industry": "Construction, Technology", "job_type": "Full Time, Part Time",
    }
    assert importer.save_job(db, values) == ("inserted", 0)
    assert importer.save_job(db, values) == ("unchanged", 0)
    saved = db.query(Job).one()
    assert (saved.category, saved.industry, saved.job_type) == (
        "Management, Engineering", "Construction, Technology", "Full Time, Part Time")
    db.close()


def test_save_job_uses_database_filtered_lookup_without_loading_all(monkeypatch, db_factory):
    db = db_factory()
    db.add(Job(title="Engineer", apply_url="https://wuzzuf.net/jobs/p/alpha", source="WUZZUF"))
    db.commit()

    from sqlalchemy.orm import Query
    monkeypatch.setattr(Query, "all", lambda self: pytest.fail("save_job must not load all jobs"))
    assert importer.save_job(db, {
        "title": "Engineer", "apply_url": "https://Wuzzuf.net/jobs/p/alpha/?ref=list",
        "source": "WUZZUF",
    }) == ("unchanged", 0)
    db.close()


class ConflictQuery:
    def __init__(self, session):
        self.session = session

    def filter(self, *args):
        return self

    def first(self):
        self.session.lookup_count += 1
        return None if self.session.lookup_count == 1 else self.session.visible_job


class ConflictSession:
    def __init__(self, visible_job=None):
        self.visible_job = visible_job
        self.lookup_count = 0
        self.rollbacks = 0
        self.commits = 0

    def query(self, model):
        assert model is Job
        return ConflictQuery(self)

    def add(self, job):
        self.pending = job

    def commit(self):
        self.commits += 1
        raise IntegrityError("INSERT", {}, RuntimeError("simulated conflict"))

    def rollback(self):
        self.rollbacks += 1


def test_concurrent_unique_conflict_is_recovered_as_unchanged():
    existing = Job(title="Engineer", apply_url="https://wuzzuf.net/jobs/p/alpha", source="WUZZUF")
    db = ConflictSession(visible_job=existing)
    assert importer.save_job(db, {
        "title": "Engineer", "apply_url": "https://wuzzuf.net/jobs/p/alpha?listing=1",
        "source": "WUZZUF",
    }) == ("unchanged", 0)
    assert db.rollbacks == 1
    assert db.lookup_count == 2


def test_concurrent_unique_conflict_can_enrich_visible_row():
    existing = Job(title="Engineer", apply_url="https://wuzzuf.net/jobs/p/alpha",
                   description="-", company_name="WUZZUF", source="WUZZUF")
    db = ConflictSession(visible_job=existing)

    def successful_second_commit():
        db.commits += 1
        if db.commits == 1:
            raise IntegrityError("INSERT", {}, RuntimeError("simulated conflict"))

    db.commit = successful_second_commit
    assert importer.save_job(db, {
        "title": "Engineer", "apply_url": existing.apply_url,
        "description": "Genuine description", "company_name": "Actual Employer",
        "source": "WUZZUF",
    }) == ("updated", 0)
    assert existing.description == "Genuine description"
    assert existing.company_name == "Actual Employer"
    assert db.rollbacks == 1
    assert db.commits == 2


def test_unrelated_integrity_error_is_rolled_back_and_reraised():
    db = ConflictSession(visible_job=None)
    with pytest.raises(IntegrityError):
        importer.save_job(db, {
            "title": "Engineer", "apply_url": "https://wuzzuf.net/jobs/p/alpha",
            "source": "WUZZUF",
        })
    assert db.rollbacks == 1
    assert db.lookup_count == 2


def test_detail_failure_isolated_and_outcomes_counted(monkeypatch, db_factory):
    listing_url = "https://wuzzuf.net/saudi/a/jobs-in-saudi-arabia"
    first = "https://wuzzuf.net/jobs/p/alpha-software-engineer"
    second = "https://wuzzuf.net/jobs/p/beta-accountant"
    http = FakeHTTP({listing_url: [Response(fixture("wuzzuf_listing.html"))], first: [Response(fixture("wuzzuf_jsonld_detail.html"))], second: [Response("broken", 404)]})
    monkeypatch.setattr(importer, "SOURCES", [source(url=listing_url)])
    result = importer.run_import(session_factory=db_factory, http_session=http, sleeper=lambda _: None)
    assert result == {"listing_links_found": 3, "unique_job_urls": 2, "inserted": 1, "updated": 0, "unchanged": 0, "failed_detail_pages": 1, "duplicate_database_urls": 0}
    db = db_factory()
    assert db.query(Job).count() == 1
    assert db.query(Job).one().company_name == "Gulf Technology Co."
    db.close()


def test_detail_403_repairs_existing_from_listing_without_inserting_new_job(
        monkeypatch, db_factory):
    listing_url = "https://wuzzuf.net/saudi/a/jobs-in-saudi-arabia"
    first = "https://wuzzuf.net/jobs/p/alpha-software-engineer"
    second = "https://wuzzuf.net/jobs/p/beta-accountant"
    db = db_factory()
    existing = Job(
        title="Keep title", description="Keep description", skills="Keep skills",
        company_name="Keep company", industry="Keep legacy industry",
        category="BadCategoryValue", job_type="Full-timePart-time",
        work_mode="Remote WorkWork from Office", apply_url=first, source="WUZZUF",
    )
    db.add(existing)
    db.commit()
    original_id = existing.id
    db.close()
    monkeypatch.setattr(importer, "SOURCES", [source(url=listing_url)])
    http = FakeHTTP({
        listing_url: [Response(fixture("wuzzuf_listing.html")), Response(fixture("wuzzuf_listing.html"))],
        first: [Response(status=403), Response(status=403)],
        second: [Response(status=403), Response(status=403)],
    })

    first_run = importer.run_import(db_factory, http, lambda _: None)
    second_run = importer.run_import(db_factory, http, lambda _: None)

    assert first_run["updated"] == 1 and first_run["inserted"] == 0
    assert first_run["failed_detail_pages"] == 2
    assert second_run["unchanged"] == 1 and second_run["updated"] == 0
    db = db_factory()
    assert db.query(Job).count() == 1
    saved = db.query(Job).one()
    assert (saved.id, saved.category, saved.job_type, saved.work_mode) == (
        original_id, "Software Development, Engineering", "Full Time", "Remote")
    assert (saved.title, saved.description, saved.skills, saved.company_name,
            saved.industry) == (
        "Keep title", "Keep description", "Keep skills", "Keep company",
        "Keep legacy industry")
    db.close()


def test_listing_classifications_override_detail_classifications_only(
        monkeypatch, db_factory):
    listing_url = "https://wuzzuf.net/saudi/a/jobs-in-saudi-arabia"
    first = "https://wuzzuf.net/jobs/p/alpha-software-engineer"
    second = "https://wuzzuf.net/jobs/p/beta-accountant"
    detail_html = fixture("wuzzuf_jsonld_detail.html")
    monkeypatch.setattr(importer, "SOURCES", [source(url=listing_url)])
    http = FakeHTTP({
        listing_url: [Response(fixture("wuzzuf_listing.html"))],
        first: [Response(detail_html)], second: [Response(detail_html)],
    })

    result = importer.run_import(db_factory, http, lambda _: None)

    assert result["inserted"] == 2
    db = db_factory()
    saved = db.query(Job).filter(Job.apply_url == first).one()
    assert (saved.category, saved.job_type, saved.work_mode) == (
        "Software Development, Engineering", "Full Time", "Remote")
    assert saved.company_name == "Gulf Technology Co."
    assert saved.description == "Build reliable services."
    assert saved.industry == "Technology"
    db.close()


def test_listing_merge_preserves_authoritative_detail_industry_and_is_idempotent(
        monkeypatch, db_factory):
    listing_url = "https://wuzzuf.net/saudi/a/jobs-in-saudi-arabia"
    job_url = "https://wuzzuf.net/jobs/p/alpha-software-engineer"
    listing_html = fixture("wuzzuf_listing.html").replace(
        'href="/jobs/p/beta-accountant/"', 'href="/companies/beta"')
    detail_html = fixture("wuzzuf_mixed_sources_detail.html").replace(
        "saudi/jobs/p/structured-sources-engineer", "jobs/p/alpha-software-engineer")
    db = db_factory()
    db.add(Job(
        title="Keep title", description="Keep description", skills="Keep skills",
        company_name="Keep company", country="Qatar", city="Keep city",
        category="Stale category", industry="Stale industry",
        job_type="Stale type", work_mode="Stale mode", salary_min="999",
        apply_url=job_url, source="WUZZUF",
    ))
    db.commit()
    original_id = db.query(Job).one().id
    db.close()
    monkeypatch.setattr(importer, "SOURCES", [source(url=listing_url)])
    http = FakeHTTP({
        listing_url: [Response(listing_html), Response(listing_html)],
        job_url: [Response(detail_html), Response(detail_html)],
    })

    first = importer.run_import(db_factory, http, lambda _: None)
    second = importer.run_import(db_factory, http, lambda _: None)

    assert first["updated"] == 1 and first["inserted"] == 0
    assert second["unchanged"] == 1 and second["updated"] == 0
    db = db_factory()
    saved = db.query(Job).one()
    assert (saved.id, saved.category, saved.job_type, saved.work_mode) == (
        original_id, "Software Development, Engineering", "Full Time", "Remote")
    assert saved.industry == "Construction, Business Services"
    assert (saved.title, saved.description, saved.skills, saved.company_name,
            saved.country, saved.city, saved.salary_min) == (
        "Keep title", "Keep description", "Keep skills", "Keep company",
        "Qatar", "Keep city", "999")
    db.close()


def test_meaningless_detail_isolated_and_later_job_continues(monkeypatch, db_factory, capsys):
    listing_url = "https://wuzzuf.net/saudi/a/jobs-in-saudi-arabia"
    first = "https://wuzzuf.net/jobs/p/alpha-software-engineer"
    second = "https://wuzzuf.net/jobs/p/beta-accountant"
    http = FakeHTTP({listing_url: [Response(fixture("wuzzuf_listing.html"))],
                     first: [Response("<h1>Software Engineer</h1>")],
                     second: [Response(fixture("wuzzuf_jsonld_detail.html"))]})
    monkeypatch.setattr(importer, "SOURCES", [source(url=listing_url)])
    result = importer.run_import(session_factory=db_factory, http_session=http, sleeper=lambda _: None)
    assert result["failed_detail_pages"] == 1
    assert result["inserted"] == 1
    assert "no meaningful WUZZUF detail enrichment" in capsys.readouterr().out
    db = db_factory()
    assert db.query(Job).count() == 1
    assert db.query(Job).one().company_name == "Gulf Technology Co."
    db.close()


def test_meaningless_detail_does_not_update_existing_record(monkeypatch, db_factory):
    listing_url = "https://wuzzuf.net/saudi/a/jobs-in-saudi-arabia"
    first = "https://wuzzuf.net/jobs/p/alpha-software-engineer"
    second = "https://wuzzuf.net/jobs/p/beta-accountant"
    html = "<h1>Software Engineer</h1><p>Jeddah, Saudi Arabia</p><h2>Job Description</h2><div>-</div>"
    http = FakeHTTP({listing_url: [Response(fixture("wuzzuf_listing.html"))],
                     first: [Response(html)],
                     second: [Response(fixture("wuzzuf_jsonld_detail.html"))]})
    db = db_factory()
    db.add(Job(title="Existing title", apply_url=first, description="-", source="WUZZUF"))
    db.commit()
    db.close()
    monkeypatch.setattr(importer, "SOURCES", [source(url=listing_url)])
    result = importer.run_import(session_factory=db_factory, http_session=http, sleeper=lambda _: None)
    assert result["failed_detail_pages"] == 1
    assert result["inserted"] == 1
    db = db_factory()
    existing = db.query(Job).filter(Job.apply_url == first).one()
    assert existing.title == "Existing title"
    assert existing.description == "-"
    db.close()


def test_temporary_failure_is_retried(monkeypatch):
    monkeypatch.setattr(importer.time, "sleep", lambda _: None)
    url = "https://wuzzuf.net/jobs/p/retry"
    http = FakeHTTP({url: [Response("", 503), Response("ok")]})
    response = importer._request(http, url, source(max_retries=1))
    assert response.text == "ok"
    assert len(http.calls) == 2


def test_parser_identities_dispatch_to_wuzzuf(monkeypatch):
    original = importer.parse_wuzzuf_listing
    calls = []

    def listing(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(importer, "parse_wuzzuf_listing", listing)
    jobs, count = importer.dispatch_listing_parser(source(), fixture("wuzzuf_listing.html"))
    assert len(calls) == 1
    assert len(jobs) == 2 and count == 3
    assert importer.get_detail_parser(source()) is importer.parse_wuzzuf_detail


def test_unsupported_listing_parser_skips_source(monkeypatch, db_factory, capsys):
    listing_url = "https://other.example/jobs"
    http = FakeHTTP({listing_url: [Response(fixture("wuzzuf_listing.html"))]})
    monkeypatch.setattr(importer, "SOURCES", [source(name="OTHER", url=listing_url, listing_parser="other")])
    result = importer.run_import(session_factory=db_factory, http_session=http, sleeper=lambda _: None)
    assert result["unique_job_urls"] == 0
    assert "Unsupported listing parser" in capsys.readouterr().out
    assert len(http.calls) == 1


def test_unsupported_detail_parser_skips_before_detail_requests(monkeypatch, db_factory, capsys):
    listing_url = "https://wuzzuf.net/saudi/a/jobs-in-saudi-arabia"
    http = FakeHTTP({listing_url: [Response(fixture("wuzzuf_listing.html"))]})
    monkeypatch.setattr(importer, "SOURCES", [source(url=listing_url, detail_parser="other")])
    result = importer.run_import(session_factory=db_factory, http_session=http, sleeper=lambda _: None)
    assert result["unique_job_urls"] == 0
    assert "Unsupported detail parser" in capsys.readouterr().out
    assert len(http.calls) == 1


def test_non_wuzzuf_source_never_uses_wuzzuf_detail_parser(monkeypatch, db_factory):
    listing_url = "https://other.example/jobs"
    http = FakeHTTP({listing_url: [Response(fixture("wuzzuf_listing.html"))]})
    calls = []

    def detail(*args):
        calls.append(args)

    monkeypatch.setattr(importer, "parse_wuzzuf_detail", detail)
    monkeypatch.setattr(importer, "SOURCES", [source(name="OTHER", url=listing_url, listing_parser="other", detail_parser="other")])
    importer.run_import(session_factory=db_factory, http_session=http, sleeper=lambda _: None)
    assert calls == []


@pytest.mark.parametrize("html, expected_title", [
    ('<script type="application/ld+json">{"@graph":[{"@type":"Organization","name":"A"},{"@type":"JobPosting","title":"Graph Job"}]}</script>', "Graph Job"),
    ('<script type="application/ld+json">{bad json</script><script type="application/ld+json">{"@type":"JobPosting","title":"After Malformed"}</script>', "After Malformed"),
    ('<script type="application/ld+json">{"@type":"Organization","name":"A"}</script><script type="application/ld+json">{"@type":"JobPosting","title":"Second Block"}</script>', "Second Block"),
    ('<script type="application/ld+json">{"@type":"JobPosting","title":"Minimal Job"}</script>', "Minimal Job"),
])
def test_structured_data_variants(html, expected_title):
    job = importer.parse_wuzzuf_detail(html, "https://wuzzuf.net/jobs/p/structured", source())
    assert job["title"] == expected_title
    assert job["company_name"] == ""
    assert job["salary_min"] == ""
    assert job["closing_date"] is None


def test_commit_failure_rolls_back_and_later_job_continues(monkeypatch, db_factory):
    listing_url = "https://wuzzuf.net/saudi/a/jobs-in-saudi-arabia"
    first = "https://wuzzuf.net/jobs/p/alpha-software-engineer"
    second = "https://wuzzuf.net/jobs/p/beta-accountant"
    http = FakeHTTP({listing_url: [Response(fixture("wuzzuf_listing.html"))],
                     first: [Response(fixture("wuzzuf_jsonld_detail.html"))],
                     second: [Response(fixture("wuzzuf_html_detail.html"))]})
    sessions = []
    calls = 0

    class FailCommitOnce:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.rolled_back = False

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

        def commit(self):
            raise RuntimeError("commit failed")

        def rollback(self):
            self.rolled_back = True
            self.wrapped.rollback()

    def sessions_factory():
        nonlocal calls
        calls += 1
        session = db_factory()
        if calls == 1:
            session = FailCommitOnce(session)
        sessions.append(session)
        return session

    monkeypatch.setattr(importer, "SOURCES", [source(url=listing_url)])
    result = importer.run_import(session_factory=sessions_factory, http_session=http, sleeper=lambda _: None)
    assert result["failed_detail_pages"] == 1
    assert result["inserted"] == 1
    assert sessions[0].rolled_back is True
    db = db_factory()
    assert db.query(Job).count() == 1
    assert db.query(Job).one().title == "Operations Manager"
    db.close()


def test_wuzzuf_filter_backfill_repairs_only_authoritative_fields(monkeypatch, db_factory):
    url = "https://wuzzuf.net/saudi/jobs/p/live-office-coordinator-riyadh-saudi-arabia"
    db = db_factory()
    legacy = Job(
        title="Keep title", description="Keep description", skills="Keep skills",
        company_name="Keep company", country="Kuwait", city="Keep city",
        category="ManagementEngineering", industry="ConstructionTechnology",
        job_type="Full-timePart-time", work_mode="Remote WorkWork from Office",
        salary_min="999", apply_url=url, source="WUZZUF",
    )
    db.add(legacy)
    db.commit()
    original_id = legacy.id
    db.close()
    monkeypatch.setattr(importer, "SOURCES", [source()])
    http = FakeHTTP({url: [Response(fixture("wuzzuf_live_english_detail.html"))]})

    result = importer.backfill_wuzzuf_filters(
        session_factory=db_factory, http_session=http, sleeper=lambda _: None)

    assert result == {"scanned": 1, "updated": 1, "unchanged": 0,
                      "skipped_missing_page": 0,
                      "skipped_no_authoritative_data": 0, "failed": 0,
                      "failure_diagnostics": {
                          "http_403": 0, "http_429": 0, "http_5xx": 0,
                          "http_other": 0, "timeout": 0,
                          "connection_error": 0,
                          "parser_or_unexpected": 0, "database": 0,
                      }}
    db = db_factory()
    saved = db.query(Job).one()
    assert (saved.category, saved.industry, saved.job_type, saved.work_mode) == (
        "Administration", "Business Services", "Full Time", "On-site")
    assert (saved.id, saved.apply_url, saved.source) == (original_id, url, "WUZZUF")
    assert (saved.title, saved.description, saved.skills, saved.company_name,
            saved.country, saved.city, saved.salary_min) == (
        "Keep title", "Keep description", "Keep skills", "Keep company",
        "Kuwait", "Keep city", "999")
    db.close()


def test_backfill_preserves_non_authoritative_field_and_is_idempotent(monkeypatch, db_factory):
    url = "https://wuzzuf.net/saudi/jobs/p/structured-sources-engineer"
    html = fixture("wuzzuf_mixed_sources_detail.html").replace(
        '"workTypes":[{"displayedName":"Full-time"},{"displayedName":"Part_time"}],', "")
    db = db_factory()
    db.add(Job(title="Engineer", apply_url=url, source="WUZZUF",
               category="Bad category", industry="Bad industry",
               job_type="Keep contract", work_mode="Bad mode"))
    db.commit()
    db.close()
    monkeypatch.setattr(importer, "SOURCES", [source()])
    http = FakeHTTP({url: [Response(html), Response(html)]})

    first = importer.backfill_wuzzuf_filters(db_factory, http, lambda _: None)
    second = importer.backfill_wuzzuf_filters(db_factory, http, lambda _: None)

    assert first["updated"] == 1
    assert second["unchanged"] == 1 and second["updated"] == 0
    db = db_factory()
    assert db.query(Job).one().job_type == "Keep contract"
    db.close()


def test_backfill_skips_gone_and_non_authoritative_pages(monkeypatch, db_factory):
    gone = "https://wuzzuf.net/jobs/p/gone"
    fallback = "https://wuzzuf.net/jobs/p/fallback"
    db = db_factory()
    db.add_all([
        Job(title="Gone", apply_url=gone, source="WUZZUF", category="Keep"),
        Job(title="Fallback", apply_url=fallback, source="WUZZUF", category="Keep"),
    ])
    db.commit()
    db.close()
    monkeypatch.setattr(importer, "SOURCES", [source()])
    http = FakeHTTP({gone: [Response(status=410)], fallback: [Response(
        '<script type="application/ld+json">{"@type":"JobPosting",'
        '"title":"Fallback","occupationalCategory":"Do not trust"}</script>')]})

    result = importer.backfill_wuzzuf_filters(db_factory, http, lambda _: None)

    assert result["skipped_missing_page"] == 1
    assert result["skipped_no_authoritative_data"] == 1
    db = db_factory()
    assert {job.category for job in db.query(Job).all()} == {"Keep"}
    db.close()


def test_backfill_failure_does_not_stop_later_job_and_ignores_other_sources(
        monkeypatch, db_factory):
    failed = "https://wuzzuf.net/jobs/p/failed"
    repaired = "https://wuzzuf.net/saudi/jobs/p/live-office-coordinator-riyadh-saudi-arabia"
    other = "https://example.com/jobs/other"
    db = db_factory()
    db.add_all([
        Job(title="Failed", apply_url=failed, source="WUZZUF", category="Keep"),
        Job(title="Repair", apply_url=repaired, source="WUZZUF", category="Bad"),
        Job(title="Other", apply_url=other, source="OTHER", category="Untouched"),
    ])
    db.commit()
    db.close()
    monkeypatch.setattr(importer, "SOURCES", [source()])
    http = FakeHTTP({
        failed: [requests.ConnectionError("network")],
        repaired: [Response(fixture("wuzzuf_live_english_detail.html"))],
    })

    result = importer.backfill_wuzzuf_filters(db_factory, http, lambda _: None)

    assert result["scanned"] == 2 and result["failed"] == 1 and result["updated"] == 1
    assert result["failure_diagnostics"]["connection_error"] == 1
    db = db_factory()
    assert db.query(Job).filter(Job.apply_url == other).one().category == "Untouched"
    assert db.query(Job).filter(Job.apply_url == repaired).one().category == "Administration"
    db.close()


@pytest.mark.parametrize(("failure", "diagnostic"), [
    (Response(status=403), "http_403"),
    (Response(status=429), "http_429"),
    (Response(status=500), "http_5xx"),
    (requests.Timeout("slow"), "timeout"),
    (requests.ConnectionError("offline"), "connection_error"),
])
def test_backfill_classifies_request_failures(
        monkeypatch, db_factory, failure, diagnostic):
    url = "https://wuzzuf.net/jobs/p/request-failure"
    db = db_factory()
    db.add(Job(title="Failure", apply_url=url, source="WUZZUF"))
    db.commit()
    db.close()
    monkeypatch.setattr(importer, "SOURCES", [source()])

    result = importer.backfill_wuzzuf_filters(
        db_factory, FakeHTTP({url: [failure]}), lambda _: None)

    assert result["failed"] == 1
    assert result["failure_diagnostics"][diagnostic] == 1
    assert sum(result["failure_diagnostics"].values()) == 1


def test_backfill_classifies_parser_or_unexpected_exception(monkeypatch, db_factory):
    url = "https://wuzzuf.net/jobs/p/parser-failure"
    db = db_factory()
    db.add(Job(title="Failure", apply_url=url, source="WUZZUF"))
    db.commit()
    db.close()
    monkeypatch.setattr(importer, "SOURCES", [source()])
    monkeypatch.setattr(
        importer, "parse_wuzzuf_detail",
        lambda *_: (_ for _ in ()).throw(ValueError("malformed structured data")),
    )

    result = importer.backfill_wuzzuf_filters(
        db_factory, FakeHTTP({url: [Response("ignored")]}), lambda _: None)

    assert result["failed"] == 1
    assert result["failure_diagnostics"]["parser_or_unexpected"] == 1


def test_backfill_classifies_database_exception(monkeypatch, db_factory):
    url = "https://wuzzuf.net/saudi/jobs/p/live-office-coordinator-riyadh-saudi-arabia"
    db = db_factory()
    db.add(Job(title="Failure", apply_url=url, source="WUZZUF"))
    db.commit()
    db.close()
    calls = 0

    class FailingDatabase:
        def query(self, *_):
            raise RuntimeError("database unavailable")

        def rollback(self):
            pass

        def close(self):
            pass

    def session_factory():
        nonlocal calls
        calls += 1
        return db_factory() if calls == 1 else FailingDatabase()

    monkeypatch.setattr(importer, "SOURCES", [source()])
    result = importer.backfill_wuzzuf_filters(
        session_factory,
        FakeHTTP({url: [Response(fixture("wuzzuf_live_english_detail.html"))]}),
        lambda _: None,
    )

    assert result["failed"] == 1
    assert result["failure_diagnostics"]["database"] == 1
