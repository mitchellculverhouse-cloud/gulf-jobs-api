from sources import SOURCES
from normalizer import allowed_country


def run_import():

    print("Starting import...")

    for source in SOURCES:

        if not source["active"]:
            continue

        print(f"Processing source: {source['name']}")

        print(f"Feed URL: {source['url']}")

    print("Import complete.")


if __name__ == "__main__":
    run_import()
