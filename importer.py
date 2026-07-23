import requests

from sources import SOURCES
from normalizer import allowed_country


def run_import():

    print("Starting import...")

    for source in SOURCES:

        if not source["active"]:
            continue

        print(f"Processing source: {source['name']}")

        try:

            response = requests.get(
                source["url"],
                timeout=30
            )

            print(
                f"Status: {response.status_code}"
            )

            print(
                f"Downloaded: {len(response.text)} characters"
            )

        except Exception as e:

            print(
                f"Error downloading source: {e}"
            )

    print("Import complete.")


if __name__ == "__main__":
    run_import()
