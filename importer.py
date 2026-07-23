import feedparser

from sources import SOURCES
from normalizer import allowed_country


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


                print("--------------------")

                print(
                    "Title:",
                    title
                )

                print(
                    "Link:",
                    link
                )


        except Exception as e:

            print(
                f"Import error: {e}"
            )


    print("\nImport complete.")


if __name__ == "__main__":
    run_import()
