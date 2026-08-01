# Job Sultan API

Job imports run automatically every six hours through the GitHub Actions workflow at
`.github/workflows/import-jobs.yml`. The workflow can also be started manually with
GitHub Actions' **Run workflow** control.

The repository must have an Actions secret named `IMPORT_API_KEY`. Its value must
match the `IMPORT_API_KEY` configured for the API on Render. Secret values must never
be committed to the repository.
