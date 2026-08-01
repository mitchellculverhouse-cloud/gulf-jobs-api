import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from database import harden_job_url_identity
from importer import save_job
from models import Job


ROOT = Path(__file__).resolve().parents[1]


def run_python(code, database_url=None, cwd=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    if database_url is None:
        env.pop("DATABASE_URL", None)
    else:
        env["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=cwd or ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def test_sqlite_fallback_and_init_db_create_jobs_table(tmp_path):
    result = run_python(
        """
import json
from sqlalchemy import inspect
import database
database.init_db()
database.init_db()
print(json.dumps({
    "backend": database.engine.url.get_backend_name(),
    "tables": inspect(database.engine).get_table_names(),
    "indexes": inspect(database.engine).get_indexes("jobs"),
    "nullable": next(column["nullable"] for column in inspect(database.engine).get_columns("jobs")
                     if column["name"] == "apply_url"),
}))
""",
        cwd=tmp_path,
    )

    details = json.loads(result.stdout.splitlines()[-1])
    assert details["backend"] == "sqlite"
    assert details["tables"] == ["jobs"]
    assert details["nullable"] is False
    assert any(index["name"] == "uq_jobs_apply_url" and index["unique"]
               for index in details["indexes"])
    assert result.stdout.splitlines()[0] == "DATABASE BACKEND: sqlite"


def test_postgresql_url_is_selected_without_printing_credentials():
    secret_url = "postgresql://stage_user:stage_password@db.example.invalid/jobs"
    result = run_python(
        """
import json
import sqlalchemy
from sqlalchemy.engine import make_url
class Pool:
    _pre_ping = True
class Engine:
    url = make_url("postgresql://placeholder/placeholder")
    pool = Pool()
sqlalchemy.create_engine = lambda url, **options: Engine()
import database
print(json.dumps({
    "backend": database.engine.url.get_backend_name(),
    "pre_ping": database.engine.pool._pre_ping,
}))
""",
        database_url=secret_url,
    )

    assert json.loads(result.stdout.splitlines()[-1]) == {
        "backend": "postgresql",
        "pre_ping": True,
    }
    assert result.stdout.splitlines()[0] == "DATABASE BACKEND: postgresql"
    assert "stage_user" not in result.stdout
    assert "stage_password" not in result.stdout
    assert "db.example.invalid" not in result.stdout
    assert result.stderr == ""


def test_sqlite_connection_arguments_are_not_applied_to_postgresql():
    result = run_python(
        """
import json
import sqlalchemy
captured = {}
class Engine:
    pass
def capture(url, **options):
    captured.update(options)
    return Engine()
sqlalchemy.create_engine = capture
import database
print(json.dumps(captured))
""",
        database_url="postgresql://user:password@db.example.invalid/jobs",
    )

    options = json.loads(result.stdout.splitlines()[-1])
    assert options == {"pool_pre_ping": True}


def test_app_and_importer_use_shared_database_model_and_session(tmp_path):
    result = run_python(
        """
import json
import app
import database
import importer
import models
app.initialize_database()
with database.Session() as session:
    session.add(models.Job(title="Shared configuration", source="Test",
                           apply_url="https://example.test/jobs/shared"))
    session.commit()
response = app.get_jobs()
print(json.dumps({
    "app_job": app.Job is models.Job,
    "importer_job": importer.Job is models.Job,
    "app_session": app.Session is database.Session,
    "importer_session": importer.Session is database.Session,
    "title": response["results"][0]["title"],
}))
""",
        cwd=tmp_path,
    )

    assert json.loads(result.stdout.splitlines()[-1]) == {
        "app_job": True,
        "importer_job": True,
        "app_session": True,
        "importer_session": True,
        "title": "Shared configuration",
    }


def test_fresh_database_rejects_null_and_duplicate_apply_urls(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'constraints.db'}"
    result = run_python(
        """
import database
from models import Job
from sqlalchemy.exc import IntegrityError
database.init_db()
results = []
for jobs in ([Job(title="Missing")], [
    Job(title="First", apply_url="https://example.test/jobs/1"),
    Job(title="Second", apply_url="https://example.test/jobs/1"),
]):
    with database.Session() as session:
        session.add_all(jobs)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            results.append(True)
print(results)
""",
        database_url=database_url,
    )
    assert result.stdout.splitlines()[-1] == "[True, True]"


def _create_legacy_database(path):
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,
                title VARCHAR,
                description TEXT,
                skills TEXT,
                country VARCHAR,
                city VARCHAR,
                area VARCHAR,
                company_name VARCHAR,
                category VARCHAR,
                industry VARCHAR,
                salary_min VARCHAR,
                salary_max VARCHAR,
                salary_currency VARCHAR,
                salary_period VARCHAR,
                job_type VARCHAR,
                work_mode VARCHAR,
                experience_level VARCHAR,
                nationality_required VARCHAR,
                gender_required VARCHAR,
                arabic_required VARCHAR,
                languages_required VARCHAR,
                date_posted VARCHAR,
                closing_date VARCHAR,
                apply_url VARCHAR,
                source VARCHAR
            )
        """))
    return engine


def test_hardening_canonicalizes_merges_and_is_repeatable(tmp_path):
    engine = _create_legacy_database(tmp_path / "legacy.db")
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO jobs
                (id, title, description, company_name, city, apply_url, source)
            VALUES
                (7, 'Retained title', NULL, 'Original employer', NULL,
                 'https://EXAMPLE.test/jobs/alpha/?tracking=old', 'SOURCE_A'),
                (12, 'Later title', 'Complementary description', 'Later employer', 'Riyadh',
                 'https://example.test/jobs/alpha', 'SOURCE_B'),
                (20, 'Separate job', 'Separate description', 'Separate employer', 'Doha',
                 'https://example.test/jobs/beta/?ref=list', 'SOURCE_B')
        """))

    harden_job_url_identity(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        jobs = session.query(Job).order_by(Job.id).all()
        assert [job.id for job in jobs] == [7, 20]
        retained, separate = jobs
        assert retained.apply_url == "https://example.test/jobs/alpha"
        assert retained.title == "Retained title"
        assert retained.company_name == "Original employer"
        assert retained.description == "Complementary description"
        assert retained.city == "Riyadh"
        assert retained.source == "SOURCE_A"
        assert separate.apply_url == "https://example.test/jobs/beta"

    indexes = inspect(engine).get_indexes("jobs")
    assert any(index["name"] == "uq_jobs_apply_url" and index["unique"]
               for index in indexes)

    before = [(job.id, job.apply_url, job.description) for job in jobs]
    harden_job_url_identity(engine)
    with factory() as session:
        after = [(job.id, job.apply_url, job.description)
                 for job in session.query(Job).order_by(Job.id)]
        assert after == before
        assert save_job(session, {
            "title": "Retained title",
            "description": "Complementary description",
            "apply_url": "https://example.test/jobs/alpha/?again=1",
            "source": "SOURCE_A",
        }) == ("unchanged", 0)
        assert session.query(Job).count() == 2


def test_hardening_rejects_missing_urls_with_count_only(tmp_path):
    engine = _create_legacy_database(tmp_path / "missing.db")
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO jobs (id, title, apply_url) VALUES "
            "(1, 'Missing', NULL), (2, 'Empty', '   ')"
        ))
    with pytest.raises(RuntimeError, match=r"2 rows have missing or empty URLs$"):
        harden_job_url_identity(engine)
