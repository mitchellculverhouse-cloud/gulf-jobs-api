import requests
import re
import json

from sources import SOURCES
from database import Session
from models import Job


def run_import():

    print("Starting import...")

    for source in SOURCES:

        if not source["active"]:
            continue

        print(f"\nProcessing source: {source['name']}")

        try:

            response = requests.get(
                source["url"],
                headers={
                    "User-Agent": "Mozilla/5.0"
                },
                timeout=30
            )

            print(
                "HTTP STATUS:",
                response.status_code
            )

            html = response.text

            print(
                "HTML LENGTH:",
                len(html)
            )


            match = re.search(
                r'"jobPosts":(\{.*?\}),"currentPageJobPosts"',
                html
            )


            if not match:

                print(
                    "JOB DATA NOT FOUND"
                )

                continue


            jobs_json = match.group(1)

            jobs = json.loads(
                jobs_json
            )


            print(
                "JOBS FOUND:",
                len(jobs)
            )


            for job_id, job in jobs.items():

                title = job.get(
                    "jb_title",
                    ""
                )


                print(
                    "PROCESSING:",
                    title
                )


                link = (
                    "https://www.bayt.com/en/job/"
                    + str(job_id)
                    + "/"
                )


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

                    description="",

                    country="Saudi Arabia",

                    category="",

                    apply_url=link,

                    source=source["name"],

                    date_posted=None

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


    print(
        "\nImport complete."
    )


if __name__ == "__main__":
    run_import()
