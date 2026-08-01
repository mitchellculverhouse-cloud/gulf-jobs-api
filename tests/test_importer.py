import pathlib

import pytest
import requests
from sqlalchemy import create_engine
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
            raise requests.HTTPError(str(self.status_code))


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


def test_existing_record_is_enriched_without_overwriting_useful_values(db_factory):
    db = db_factory()
    db.add(Job(title="Original title", apply_url="https://WUZZUF.net/jobs/p/alpha/?ref=old", description="Keep me", company_name=""))
    db.commit()
    outcome, duplicates = importer.save_job(db, {"title": "Scraped title", "apply_url": "https://wuzzuf.net/jobs/p/alpha", "description": "Replacement", "company_name": "Actual Employer", "source": "WUZZUF"})
    saved = db.query(Job).one()
    assert (outcome, duplicates) == ("updated", 0)
    assert saved.id == 1
    assert saved.title == "Original title"
    assert saved.description == "Keep me"
    assert saved.company_name == "Actual Employer"
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


def test_repeated_save_prevents_new_duplicate_and_reports_existing_duplicates(db_factory):
    db = db_factory()
    values = {"title": "Engineer", "apply_url": "https://wuzzuf.net/jobs/p/alpha", "source": "WUZZUF"}
    assert importer.save_job(db, values)[0] == "inserted"
    assert importer.save_job(db, values)[0] == "unchanged"
    db.add(Job(title="Historic duplicate", apply_url=values["apply_url"]))
    db.commit()
    assert importer.save_job(db, values) == ("unchanged", 1)
    assert db.query(Job).count() == 2
    db.close()


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
