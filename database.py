import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, NoSuchModuleError
from sqlalchemy.orm import sessionmaker

from models import Base, Job
from normalizer import canonical_url


DEFAULT_DATABASE_URL = "sqlite:///jobs.db"
database_url = os.environ.get("DATABASE_URL")
if database_url is None:
    database_url = DEFAULT_DATABASE_URL

try:
    url = make_url(database_url)
except ArgumentError:
    raise RuntimeError("DATABASE_URL is not a valid SQLAlchemy database URL") from None

backend = url.get_backend_name()
engine_options = {}
if backend == "sqlite":
    engine_options["connect_args"] = {"check_same_thread": False}
elif backend == "postgresql":
    engine_options["pool_pre_ping"] = True
else:
    raise RuntimeError(f"Unsupported database backend: {backend}")

try:
    engine = create_engine(database_url, **engine_options)
except (ImportError, NoSuchModuleError):
    raise RuntimeError(f"The SQLAlchemy driver for {backend} is not installed") from None

Session = sessionmaker(bind=engine)

print(f"DATABASE BACKEND: {backend}")


def _missing(value):
    return value is None or (isinstance(value, str) and not value.strip())


def harden_job_url_identity(bind=engine):
    """Canonicalize legacy URLs, merge duplicates, and enforce URL identity."""
    session_factory = sessionmaker(bind=bind)
    with session_factory() as session:
        jobs = session.query(Job).order_by(Job.id).all()
        missing_count = sum(_missing(job.apply_url) for job in jobs)
        if missing_count:
            raise RuntimeError(
                f"Cannot harden jobs.apply_url: {missing_count} rows have missing or empty URLs"
            )

        retained_by_url = {}
        redundant = []
        merge_fields = [column.key for column in Job.__table__.columns
                        if column.key not in {"id", "apply_url"}]
        for job in jobs:
            normalized_url = canonical_url(job.apply_url)
            retained = retained_by_url.get(normalized_url)
            if retained is None:
                retained_by_url[normalized_url] = job
                continue
            for field in merge_fields:
                if _missing(getattr(retained, field)) and not _missing(getattr(job, field)):
                    setattr(retained, field, getattr(job, field))
            redundant.append(job)

        for job in redundant:
            session.delete(job)
        session.flush()
        for normalized_url, retained in retained_by_url.items():
            retained.apply_url = normalized_url
        session.commit()

    apply_url_index = next(
        index for index in Job.__table__.indexes if index.name == "uq_jobs_apply_url"
    )
    with bind.begin() as connection:
        apply_url_index.create(bind=connection, checkfirst=True)
        if bind.dialect.name == "postgresql":
            nullable = next(
                column["nullable"] for column in inspect(connection).get_columns("jobs")
                if column["name"] == "apply_url"
            )
            if nullable:
                connection.execute(
                    text("ALTER TABLE jobs ALTER COLUMN apply_url SET NOT NULL")
                )


def init_db():
    Base.metadata.create_all(bind=engine)
    harden_job_url_identity(engine)
