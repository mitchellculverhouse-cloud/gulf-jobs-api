# WUZZUF listing classification

WUZZUF listing pages expose job-associated `workRoles`, `workTypes`, and
`workplaceArrangement` values in their server-rendered state. The importer
matches each state entity to its canonical job URL and treats only those three
values as authoritative for category, job type, and work mode respectively.

Listing state does not provide a sufficiently trustworthy employer-industry
source. The importer therefore does not derive industry from roles, titles,
keywords, or company names. Stored legacy WUZZUF industry data is retained, but
WUZZUF industry values are omitted from public filter options. Industries from
other sources and employer-posted jobs remain eligible for filter options.

Detail pages remain required when inserting a new WUZZUF job. If a detail
request fails, including with HTTP 403, listing classifications may repair an
already-existing row; a listing-only row is never inserted.
