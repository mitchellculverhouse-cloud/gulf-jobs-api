from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import Job


engine = create_engine("sqlite:///jobs.db")

Session = sessionmaker(bind=engine)
