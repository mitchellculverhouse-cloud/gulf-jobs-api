class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)

    title = Column(String)
    description = Column(Text)
    skills = Column(Text)

    country = Column(String)
    city = Column(String)
    area = Column(String)

    company_name = Column(String)

    category = Column(String)
    industry = Column(String)

    salary_min = Column(String)
    salary_max = Column(String)
    salary_currency = Column(String)
    salary_period = Column(String)

    job_type = Column(String)
    work_mode = Column(String)
    experience_level = Column(String)

    nationality_required = Column(String)
    gender_required = Column(String)
    arabic_required = Column(String)
    languages_required = Column(String)

    date_posted = Column(String)
    closing_date = Column(String)

    apply_url = Column(String)
    source = Column(String)


Session = sessionmaker(bind=engine)
