# WUZZUF listing classification

WUZZUF listing pages expose job-associated classifications both in listing-card
HTML and, on some page versions, in `workRoles`, `workTypes`, and
`workplaceArrangement` values in embedded state. The importer matches embedded
entities by canonical job URL and also reads exact taxonomy labels only from the
HTML card containing that URL. A finite WUZZUF category vocabulary prevents
skill links on the same card from becoming categories. Missing work mode stays
missing.

Listing state does not provide a sufficiently trustworthy employer-industry
source. The importer therefore does not derive industry from roles, titles,
keywords, or company names. Stored legacy WUZZUF industry data is retained, but
WUZZUF industry values are omitted from public filter options. Industries from
other sources and employer-posted jobs remain eligible for filter options.

Detail pages remain required when inserting a new WUZZUF job. If a detail
request fails, including with HTTP 403, listing classifications may repair an
already-existing row; a listing-only row is never inserted.
