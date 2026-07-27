import feedparser

from sources import SOURCES

from database import Session
from models import Job


def run_import():

    print("Starting import...")

    for source in SOURCES:

        if not source["active"]:
            continue

        print(
            f"\nProcessing source: {source['name']}"
        )

        session = None

        try:

            feed = feedparser.parse(
                source["url"]
            )

            print(
                "Feed title:",
                feed.feed.get("title")
            )

            print(
                "Feed entries:",
                len(feed.entries)
            )


            for item in feed.entries:

                title = item.get(
                    "title",
                    ""
                )

                link = item.get(
                    "link",
                    ""
                )

                description = item.get(
                    "description",
                    ""
                )


                print(
                    "PROCESSING:",
                    title
                )


                if not title or not link:

                    print(
                        "Skipped - missing title or link"
                    )

                    continue


                session = Session()


                exists = session.query(Job).filter(
                    Job.apply_url == link
                ).first()


                if exists:

                    print(
                        "Duplicate skipped:",
                        title
                    )

                    session.close()

                    continue


                new_job = Job(

                    title=title,

                    description=description,

                    skills="",

                    country="",

                    city="",

                    area="",

                    company_name=source["name"],

                    category="",

                    industry="",

                    salary_min="",

                    salary_max="",

                    salary_currency="",

                    salary_period="",

                    job_type="",

                    work_mode="",

                    experience_level="",

                    nationality_required="",

                    gender_required="",

                    arabic_required="",

                    languages_required="",

                    date_posted=None,

                    closing_date=None,

                    apply_url=link,

                    source=source["name"]

                )


                session.add(
                    new_job
                )

                session.commit()

                session.close()


                print(
                    "Saved:",
                    title
                )


        except Exception as e:

            print(
                "Import error:",
                e
            )

            if session:

                session.close()


    print(
        "\nImport complete."
    )


if __name__ == "__main__":
    run_import()
