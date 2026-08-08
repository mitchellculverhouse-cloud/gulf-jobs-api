import json
from pathlib import Path

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import importer
from models import Base, Job
from sources import SOURCES


FIXTURE = Path(__file__).parent / "fixtures" / "lever_postings.json"
RICH_FIXTURE = Path(__file__).parent / "fixtures" / "lever_flow_rich_posting.json"


class Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(str(self.status_code))
            error.response = self
            raise error


class FakeHTTP:
    def __init__(self, responses):
        self.responses = {url: list(items) for url, items in responses.items()}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        result = self.responses[url].pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def lever_source(**changes):
    source = next(dict(item) for item in SOURCES if item["name"] == "Lever - Flow")
    source.update(active=True, max_retries=0)
    source.update(changes)
    return source


def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lever.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def rich_posting():
    return json.loads(RICH_FIXTURE.read_text())


def test_rich_description_assembles_plain_lists_and_additional_content_in_order():
    job = importer.parse_lever_posting(rich_posting(), lever_source())
    description = job["description"]

    expected_order = [
        "About the Company", "About the Role", "Responsibilities",
        "Qualifications", "Why Join Flow?",
    ]
    assert [description.index(value) for value in expected_order] == sorted(
        description.index(value) for value in expected_order
    )
    assert "Own the regional operating plan and delivery." in description
    assert "Partner with Product & Sales." in description
    assert "- Lead weekly planning\n- Improve service quality" in description
    assert "- 5+ years in operations\n- Clear written communication" in description
    assert "Help shape the future of business finance." in description
    assert "&nbsp;" not in description
    assert "&amp;" not in description
    assert not any(tag in description for tag in ("<div", "<p", "<li", "<strong"))
    assert "\n\n\n" not in description
    assert job["_provider"] == "lever"
    assert job["_replace_fields"] == ("description",)


def test_missing_null_and_non_list_lists_preserve_other_description_components():
    posting = rich_posting()
    for lists in (None, {"text": "not a list"}, "malformed"):
        candidate = {**posting, "lists": lists}
        description = importer.parse_lever_posting(candidate, lever_source())["description"]
        assert description == (
            "About the Company\n\nFlow builds better financial products for businesses.\n\n"
            "Why Join Flow?\n\nHelp shape the future of business finance."
        )

    missing = dict(posting)
    missing.pop("lists")
    assert importer.parse_lever_posting(missing, lever_source())["description"] == description


def test_malformed_list_sections_are_skipped_without_aborting_valid_sections():
    posting = rich_posting()
    valid = posting["lists"][1]
    posting["lists"] = [
        None, "bad", {}, {"text": "", "content": "<p>orphan</p>"},
        {"text": "No content"}, {"text": "Wrong type", "content": {"html": "bad"}},
        valid,
    ]

    job = importer.parse_lever_posting(posting, lever_source())

    assert job is not None
    assert "Responsibilities" in job["description"]
    assert "orphan" not in job["description"]
    assert "No content" not in job["description"]
    assert "Wrong type" not in job["description"]


def test_missing_additional_plain_does_not_prevent_rich_description():
    posting = rich_posting()
    posting.pop("additionalPlain")

    description = importer.parse_lever_posting(posting, lever_source())["description"]

    assert "About the Role" in description
    assert "Why Join Flow?" not in description


def test_existing_lever_description_is_repaired_in_place_then_unchanged(tmp_path):
    sessions = database(tmp_path)
    posting = rich_posting()
    sparse = {key: value for key, value in posting.items() if key not in ("lists", "additionalPlain")}

    with sessions() as db:
        sparse_values = importer.parse_lever_posting(sparse, lever_source())
        assert importer.save_job(db, sparse_values)[0] == "inserted"
        original = db.query(Job).one()
        original_id = original.id
        assert original.description == posting["descriptionPlain"]

        rich_values = importer.parse_lever_posting(posting, lever_source())
        assert importer.save_job(db, rich_values)[0] == "updated"
        repaired = db.query(Job).one()
        assert repaired.id == original_id
        assert "Responsibilities" in repaired.description
        assert importer.save_job(db, rich_values)[0] == "unchanged"
        assert db.query(Job).count() == 1


def test_empty_or_malformed_lever_description_never_replaces_existing(tmp_path):
    sessions = database(tmp_path)
    posting = rich_posting()
    with sessions() as db:
        rich_values = importer.parse_lever_posting(posting, lever_source())
        assert importer.save_job(db, rich_values)[0] == "inserted"
        original_description = db.query(Job).one().description

        for bad_description in (None, "  ", {"unexpected": "value"}):
            malformed = {**posting, "descriptionPlain": bad_description,
                         "lists": "bad", "additionalPlain": None}
            values = importer.parse_lever_posting(malformed, lever_source())
            assert values["description"] == ""
            assert "_replace_fields" not in values
            assert importer.save_job(db, values)[0] == "unchanged"
            assert db.query(Job).one().description == original_description


def test_description_replacement_metadata_is_lever_scoped_and_not_persisted(tmp_path):
    sessions = database(tmp_path)
    posting = rich_posting()
    with sessions() as db:
        values = importer.parse_lever_posting(posting, lever_source())
        assert importer.save_job(db, values)[0] == "inserted"
        row = db.query(Job).one()
        original = row.description
        assert not hasattr(row, "_replace_fields")

        untrusted = {field: getattr(row, field) for field in importer.JOB_FIELDS}
        untrusted.update(description="A different meaningful WUZZUF description",
                         source="WUZZUF", _replace_fields=("description",))
        assert importer.save_job(db, untrusted)[0] == "unchanged"
        assert db.query(Job).one().description == original


def test_parse_realistic_feed_maps_only_explicit_gcc_postings():
    jobs, count = importer.parse_lever_feed(FIXTURE.read_text(), lever_source())

    assert count == 15
    assert [job["country"] for job in jobs] == [
        "Saudi Arabia", "United Arab Emirates", "Qatar", "Kuwait",
        "Bahrain", "Oman", "Saudi Arabia",
    ]
    assert [job["title"] for job in jobs][-1] == "Later Valid Role"
    sa = jobs[0]
    assert sa["company_name"] == "Flow"
    assert sa["city"] == "Riyadh"
    assert sa["description"] == "Lead regional operations.\nWork with the local team."
    assert sa["apply_url"] == "https://jobs.lever.co/flowlife/sa-role/apply"
    assert sa["apply_url"] != "https://jobs.lever.co/flowlife/sa-role"
    assert (sa["job_type"], sa["work_mode"]) == ("Full Time", "On-site")
    assert (sa["salary_min"], sa["salary_max"], sa["salary_currency"],
            sa["salary_period"]) == ("18000", "24000", "SAR", "Monthly")
    assert jobs[1]["job_type"] == "Part Time"
    assert jobs[1]["work_mode"] == "Hybrid"
    assert jobs[2]["job_type"] == "Internship"
    assert jobs[2]["work_mode"] == "Remote"
    assert jobs[3]["job_type"] == "Contract"
    assert jobs[4]["job_type"] == "Temporary"
    assert jobs[5]["job_type"] == "Special engagement"
    assert jobs[5]["work_mode"] == ""
    assert jobs[5]["salary_min"] == jobs[5]["salary_max"] == ""


def test_country_is_never_inferred_and_required_values_are_validated():
    base = {"text": "Saudi Nationals Only", "categories": {"location": "Riyadh"},
            "applyUrl": "https://jobs.lever.co/example/role/apply"}
    source = lever_source()

    assert importer.parse_lever_posting(base, source) is None
    assert importer.parse_lever_posting({**base, "country": "Saudi Arabia"}, source) is None
    assert importer.parse_lever_posting({**base, "country": "US"}, source) is None
    assert importer.parse_lever_posting({**base, "country": "SA", "text": " "}, source) is None
    assert importer.parse_lever_posting({**base, "country": "SA", "applyUrl": "not-a-url"}, source) is None
    assert importer.parse_lever_posting({**base, "country": "SA"},
                                        lever_source(company_name="")) is None
    job = importer.parse_lever_posting({**base, "country": "SA"}, source)
    assert job["nationality_required"] == ""
    unsupported = ("category", "industry", "skills", "experience_level",
                   "gender_required", "arabic_required", "languages_required",
                   "date_posted", "closing_date", "area")
    assert all(not job[field] for field in unsupported)


def test_malformed_country_values_do_not_stop_a_later_valid_posting(
        tmp_path, monkeypatch):
    source = lever_source()
    postings = [
        {"text": "Numeric country", "country": 123,
         "categories": {"location": "Riyadh"},
         "applyUrl": "https://jobs.lever.co/example/numeric/apply"},
        {"text": "Object country", "country": {"code": "SA"},
         "categories": {"location": "Riyadh"},
         "applyUrl": "https://jobs.lever.co/example/object/apply"},
        {"text": "List country", "country": ["SA"],
         "categories": {"location": "Riyadh"},
         "applyUrl": "https://jobs.lever.co/example/list/apply"},
        {"text": "Later valid role", "country": "SA",
         "categories": {"location": "Jeddah"},
         "applyUrl": "https://jobs.lever.co/example/valid/apply"},
    ]
    body = json.dumps(postings)

    jobs, count = importer.parse_lever_feed(body, source)
    assert count == 4
    assert [(job["title"], job["country"], job["city"]) for job in jobs] == [
        ("Later valid role", "Saudi Arabia", "Jeddah")]

    http = FakeHTTP({source["url"]: [Response(body)]})
    sessions = database(tmp_path)
    monkeypatch.setattr(importer, "SOURCES", [source])
    result = importer.run_import(sessions, http, lambda _: None)

    assert result["listing_links_found"] == 4
    assert result["unique_job_urls"] == result["inserted"] == 1
    assert result["failed_detail_pages"] == 0
    with sessions() as db:
        row = db.query(Job).one()
        assert (row.title, row.country, row.city) == (
            "Later valid role", "Saudi Arabia", "Jeddah")


def test_lever_run_uses_one_feed_request_and_is_idempotent(tmp_path, monkeypatch):
    source = lever_source()
    text = FIXTURE.read_text()
    http = FakeHTTP({source["url"]: [Response(text), Response(text)]})
    sessions = database(tmp_path)
    monkeypatch.setattr(importer, "SOURCES", [source])

    first = importer.run_import(sessions, http, lambda _: None)
    second = importer.run_import(sessions, http, lambda _: None)

    assert first == {
        "listing_links_found": 15, "unique_job_urls": 7, "inserted": 7,
        "updated": 0, "unchanged": 0, "failed_detail_pages": 0,
        "duplicate_database_urls": 0,
    }
    assert second["inserted"] == second["updated"] == 0
    assert second["unchanged"] == 7
    assert http.calls == [source["url"], source["url"]]
    with sessions() as db:
        assert db.query(Job).count() == 7


def test_same_canonical_url_enriches_without_changing_id(tmp_path):
    sessions = database(tmp_path)
    source = lever_source()
    sparse = {"text": "Engineer", "country": "AE",
              "applyUrl": "https://jobs.lever.co/acme/role/apply?tracking=1"}
    rich = {**sparse, "descriptionPlain": "Meaningful description",
            "categories": {"location": "Dubai", "commitment": "Full-time"},
            "applyUrl": "https://jobs.lever.co/acme/role/apply"}
    with sessions() as db:
        assert importer.save_job(db, importer.parse_lever_posting(sparse, source))[0] == "inserted"
        original_id = db.query(Job).one().id
        assert importer.save_job(db, importer.parse_lever_posting(rich, source))[0] == "updated"
        row = db.query(Job).one()
        assert row.id == original_id
        assert (row.description, row.city, row.job_type) == (
            "Meaningful description", "Dubai", "Full Time")


def test_bad_lever_sources_fail_independently_without_detail_failures(tmp_path, monkeypatch):
    bad_shape = lever_source(name="Lever - Bad Shape", url="https://bad.test/feed")
    bad_json = lever_source(name="Lever - Bad JSON", url="https://json.test/feed")
    failed = lever_source(name="Lever - Failed", url="https://failed.test/feed")
    good = lever_source(name="Lever - Good", company_name="Trusted Co",
                        url="https://good.test/feed")
    body = json.dumps([{"text": "Valid", "country": "OM",
                        "applyUrl": "https://jobs.lever.co/good/valid/apply"}])
    http = FakeHTTP({
        bad_shape["url"]: [Response("{}")],
        bad_json["url"]: [Response("not json")],
        failed["url"]: [Response("", 500)],
        good["url"]: [Response(body)],
    })
    monkeypatch.setattr(importer, "SOURCES", [bad_shape, bad_json, failed, good])

    result = importer.run_import(database(tmp_path), http, lambda _: None)

    assert result["listing_links_found"] == result["unique_job_urls"] == 1
    assert result["inserted"] == 1
    assert result["failed_detail_pages"] == 0
    assert http.calls == [item["url"] for item in (bad_shape, bad_json, failed, good)]


def test_production_source_activation_and_trusted_metadata():
    by_name = {source["name"]: source for source in SOURCES}
    assert [(name, by_name[name]["active"]) for name in (
        "WUZZUF", "Bayt", "GulfTalent", "Naukrigulf")
    ] == [("WUZZUF", True), ("Bayt", False), ("GulfTalent", False),
          ("Naukrigulf", False)]
    assert {name: (by_name[name]["active"], by_name[name]["company_name"])
            for name in ("Lever - Flow", "Lever - Trendyol", "Lever - Contentsquare")} == {
        "Lever - Flow": (True, "Flow"),
        "Lever - Trendyol": (True, "Trendyol"),
        "Lever - Contentsquare": (True, "Contentsquare"),
    }
