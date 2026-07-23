from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Job

engine = create_engine("sqlite:///jobs.db")

Session = sessionmaker(bind=engine)
