import feedparser

from sources import SOURCES
from normalizer import normalize_country, allowed_country


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


                print("--------------------")

                print(
                    "Title:",
                    title
                )

                print(
                    "Link:",
                    link
                )

                print(
                    "Category:",
                    category
                )

                print(
                    "Country:",
                    normalized_country
                )


        except Exception as e:

            print(
                f"Import error: {e}"
            )


    print("\nImport complete.")


if __name__ == "__main__":
    run_import()
