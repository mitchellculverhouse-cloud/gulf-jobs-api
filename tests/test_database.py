import json
import os
import subprocess
import sys
from pathlib import Path


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
print(json.dumps({
    "backend": database.engine.url.get_backend_name(),
    "tables": inspect(database.engine).get_table_names(),
}))
""",
        cwd=tmp_path,
    )

    details = json.loads(result.stdout.splitlines()[-1])
    assert details == {"backend": "sqlite", "tables": ["jobs"]}
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
    session.add(models.Job(title="Shared configuration", source="Test"))
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
