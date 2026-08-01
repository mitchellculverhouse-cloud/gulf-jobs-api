import os

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, NoSuchModuleError
from sqlalchemy.orm import sessionmaker

from models import Base


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


def init_db():
    Base.metadata.create_all(bind=engine)
