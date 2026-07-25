from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Job
import os

print(
    "DATABASE LOCATION:",
    os.path.abspath("jobs.db")
)

engine = create_engine("sqlite:///jobs.db")

Session = sessionmaker(bind=engine)
