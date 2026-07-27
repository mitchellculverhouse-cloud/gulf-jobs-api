import requests
import feedparser

from sources import SOURCES

from database import Session
from models import Job

from bs4 import BeautifulSoup
from urllib.parse import urljoin


def run_import():

    print("Starting import...")


    for source in SOURCES:

        if not source["active"]:
            continue


        print(
            f"\nProcessing source: {source['name']}"
        )

        try:

            response = requests.get(

                source["url"],

                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                },

                timeout=45

            )


            print(
                "HTTP STATUS:",
                response.status_code
            )

            print(
                "CONTENT TYPE:",
                response.headers.get("content-type")
            )


            if response.status_code != 200:

                print(
                    "SOURCE FAILED:",
                    source["name"]
                )

                continue


            print(
                "RESPONSE LENGTH:",
                len(response.text)
            )

            print(
                "RESPONSE START:",
                response.text[:500]
            )


            if source["type"] == "rss":

                feed = feedparser.parse(
                    response.text
                )

                jobs = []

                for item in feed.entries:

                    jobs.append({

                        "title": item.get(
                            "title",
                            ""
                        ),

                        "link": item.get(
                            "link",
                            ""
                        ),

                        "description": item.get(
                            "description",
                            ""
                        ),

                        "location": "",

                        "department": ""

                    })


            elif source["type"] == "html":

                soup = BeautifulSoup(
                    response.text,
                    "html.parser"
                )


                jobs = []


                rows = soup.select(
                    "tr.data-row"
                )


                print(
                    "HTML jobs found:",
                    len(rows)
                )


                for row in rows:

                    title_element = row.select_one(
                        "a.jobTitle-link"
                    )


                    if not title_element:

                        continue


                    location_element = row.select_one(
                        ".jobLocation"
                    )


                    department_element = row.select_one(
                        ".jobDepartment"
                    )


                    jobs.append({

                        "title": title_element.get_text(
                            strip=True
                        ),

                        "link": urljoin(
                            source["url"],
                            title_element["href"]
                        ),

                        "description": "",

                        "location": (
                            location_element.get_text(strip=True)
                            if location_element
                            else ""
                        ),

                        "department": (
                            department_element.get_text(strip=True)
                            if department_element
                            else ""
                        )

                    })


            else:

                print(
                    "Unknown source type:",
                    source["type"]
                )

                continue



            for job in jobs:


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


                print(
                    "PROCESSING:",
                    title
                )


                if not title or not link:

                    print(
                        "Skipped missing title/link"
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



        except requests.exceptions.Timeout:

            print(
                "TIMEOUT:",
                source["name"]
            )

            continue



        except Exception as e:

            print(
                "Import error:",
                source["name"],
                e
            )

            continue



    print(
        "\nImport complete."
    )



if __name__ == "__main__":

    run_import()
