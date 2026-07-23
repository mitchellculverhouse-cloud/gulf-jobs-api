import feedparser

from sources import SOURCES
from normalizer import normalize_country, allowed_country

from database import Session
from models import Job

def run_import():

    print("Starting import...")

    for source in SOURCES:

        if not source["active"]:
            continue

        print(f"\nProcessing source: {source['name']}")

        try:

            feed = feedparser.parse(source["url"])

            print(
                f"Jobs found: {len(feed.entries)}"
            )


            for job in feed.entries:

                title = job.get(
                    "title",
                    ""
                )

                link = job.get(
                    "link",
                    ""
                )

                description = job.get(
                    "description",
                    ""
                )

                category = job.get(
                    "category",
                    ""
                )


                country = None


                if "Job Location:" in description:

                    country = description.split(
                        "Job Location:"
                    )[1].split(
                        "</td>"
                    )[0]

                    country = country.replace(
                        "<td>",
                        ""
                    ).strip()


                normalized_country = normalize_country(
                    country
                )


                if not allowed_country(country):

                    print(
                        "Skipped:",
                        country
                    )

                    continue


                session = Session()


                existing_job = session.query(Job).filter(
                    Job.apply_url == link
                ).first()


                if existing_job:

                    print(
                        "Duplicate skipped:",
                        title
                    )

                    session.close()

                    continue


                new_job = Job(

                    title=title,

                    description=description,

                    country=normalized_country,

                    category=category,

                    apply_url=link,

                    source=source["name"],

                    date_posted=None

                )


                session.add(new_job)

                session.commit()

                session.close()


                print("--------------------")

                print(
                    "Saved:",
                    title
                )

                print(
                    "Country:",
                    normalized_country
                )

                print(
                    "Source:",
                    source["name"]
                )


        except Exception as e:

            print(
                f"Import error: {e}"
            )


    print("\nImport complete.")


if __name__ == "__main__":
    run_import()
