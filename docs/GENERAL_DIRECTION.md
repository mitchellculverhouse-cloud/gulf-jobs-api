# Job Sultan — General Direction and Master Build-Out Plan

## 1. Project Vision

Job Sultan is a multi-country Gulf job-board platform.

The platform should:

* Import jobs automatically from multiple external sources
* Support all six intended Gulf countries
* Normalise job information into one consistent database structure
* Allow users to search, filter and sort jobs
* Display useful job cards on the Job Sultan website
* Provide internal job-detail pages
* Send users to the original source only when they click the Apply button
* Support future user accounts, alerts, newsletters, matching and employer services
* Remain capable of adding additional countries, job sources and infrastructure later

The platform must not be hardcoded around one country, one job source or one external website.

WUZZUF Saudi Arabia is one source configuration. It is not the entire platform.

---

# Phase 1 — Get the Core Job Engine Stable

Use one manageable source first, then build the architecture so additional sources and countries can be added later.

Phase 1 should deliver a complete working job board, not merely a collection of job links.

## 1.1 Job Importing

The importer should support multiple source types and source configurations.

Each source should be configurable with information such as:

* Source name
* Source type
* Source URL
* Country
* Language
* Active or inactive status
* Source-specific parsing rules where required

The importing system should remain source-agnostic wherever practical.

Adding a new source should not require redesigning the entire importer.

## 1.2 Two-Stage Job Scraping

For sources such as WUZZUF, the importer should use a two-stage process.

### Stage 1 — Listing-Page Extraction

The listing-page scraper should:

* Request the job listing page
* Identify individual job listings
* Extract the job title
* Extract the canonical job-detail URL
* Deduplicate listing-page results by URL
* Continue processing if an individual listing is malformed
* Support pagination when the source contains multiple listing pages

### Stage 2 — Job-Detail Enrichment

The importer should automatically visit each individual job-detail URL.

No manual opening of job pages should be required.

The detail-page scraper should extract all information genuinely available from the source, including:

* Job title
* Company name
* Country
* City
* Area or detailed location
* Full job description
* Skills
* Category
* Department
* Industry
* Salary minimum
* Salary maximum
* Salary currency
* Salary period
* Job type
* Work mode
* Experience level
* Nationality requirements
* Gender requirements
* Arabic-language requirements
* Other language requirements
* Date posted
* Closing date
* Original application URL
* Source name

The scraper must not invent information.

When a field is unavailable, it should remain empty or use the platform’s agreed missing-value convention.

Examples include:

* Salary not disclosed
* Location not provided
* Closing date not provided

## 1.3 Scraper Reliability

The scraper should include:

* Browser-like request headers
* Request timeouts
* Retry handling for temporary failures
* A polite delay or rate limit between detail-page requests
* Clear logs
* Graceful handling of unavailable pages
* Isolation of individual failures
* Continuation when one job fails
* Source-health reporting
* Protection against one source stopping the entire import process

Where available, the scraper should prefer stable data sources such as:

* JSON-LD structured data
* Embedded page JSON
* Semantic HTML attributes
* Stable URL patterns
* Stable element attributes

Fragile generated CSS class names should only be used when no more reliable option exists.

## 1.4 Normalisation

All imported jobs should be converted into the same internal data structure regardless of their original source.

Normalisation should include:

* Consistent country names
* Consistent city names
* Consistent job-type values
* Consistent work-mode values
* Consistent experience levels
* Consistent salary currencies
* Consistent salary periods
* Clean text formatting
* Canonical application URLs
* Removal of unnecessary HTML where appropriate
* Preservation of useful description formatting

The six-country Gulf structure must remain intact.

The importer must not treat all jobs as Saudi Arabian jobs merely because the first active source is WUZZUF Saudi Arabia.

## 1.5 Duplicate Detection and Updating

Jobs should be uniquely identified using the best stable identifier available.

For the current architecture, the canonical `apply_url` may be used as the primary duplicate identifier.

Repeated imports should not create duplicate records.

Duplicate handling should work as follows:

* Insert genuinely new jobs
* Skip unnecessary recreation of fully enriched jobs
* Update existing incomplete jobs with newly available details
* Refresh selected fields when the source has changed
* Preserve internal job IDs where possible
* Avoid deleting valid data merely because a later scrape omitted a field
* Log whether each job was inserted, updated, skipped or failed

Existing basic jobs containing only a title and URL should be enriched automatically rather than permanently skipped.

## 1.6 Database

The database should store the full normalised job record.

Relevant fields include:

* Internal job ID
* Title
* Description
* Skills
* Country
* City
* Area
* Company name
* Category
* Department
* Industry
* Salary minimum
* Salary maximum
* Salary currency
* Salary period
* Job type
* Work mode
* Experience level
* Nationality requirement
* Gender requirement
* Arabic requirement
* Language requirements
* Date posted
* Closing date
* Apply URL
* Source
* Import date
* Last updated date
* Job status
* Enrichment status where useful

The database design should support:

* Search
* Filtering
* Sorting
* Pagination
* Job-detail pages
* Expiry handling
* Source management
* Future user features
* Future analytics

The production database should use persistent storage.

Temporary or ephemeral storage must not be relied upon for long-term production job data.

## 1.7 API and Backend

The API should provide structured access to jobs.

The backend should support:

* Retrieving a paginated list of jobs
* Retrieving one job by internal ID
* Keyword search
* Filtering
* Sorting
* Pagination
* Latest-job queries
* Country-specific queries
* Category-specific queries
* Validation of requested filters and sort options
* Controlled handling of missing values

Suggested routes include:

* `GET /jobs`
* `GET /jobs/{job_id}`
* `GET /api/jobs`
* `GET /api/jobs/{job_id}`

The exact route structure may follow the application’s existing framework, but the responsibilities should remain clear.

## 1.8 Search

Users should be able to search using combinations of:

* Job title
* Keywords
* Company name
* Skills
* Category
* Industry
* Country
* City
* Location

Search should work together with filters, sorting and pagination.

Search should not be a separate disconnected feature.

Homepage searches should lead to the dedicated jobs results page.

Example:

```text
/jobs?q=engineer&country=saudi-arabia
```

Search and filter selections should be represented in the URL wherever practical so users can:

* Bookmark searches
* Share searches
* Return to previous results
* Use browser back and forward navigation

## 1.9 Filters

The jobs-results page should support available filters such as:

* Country
* City
* Area or location
* Category
* Industry
* Company
* Job type
* Work mode
* Experience level
* Salary range
* Salary currency
* Date posted
* Language requirements
* Nationality requirement where relevant
* Gender requirement where relevant

Country filtering must preserve all six intended Gulf countries.

Users should be able to:

* Apply one filter
* Apply multiple filters
* See active filters
* Remove one active filter
* Clear all filters
* Change filters without losing the search query
* Move between pages without losing filters
* Change sorting without losing filters

Where practical, filter options should reflect actual available data.

A database field existing does not by itself mean filtering is complete.

Each filter should be traced through:

* Database model
* Backend query
* API parameter
* Frontend control
* Displayed results
* URL state

## 1.10 Sorting

Filtering and sorting are separate systems.

Filtering narrows the result set.

Sorting changes the order of the result set.

The jobs-results page should support sorting options such as:

* Newest first
* Oldest first
* Most relevant
* Salary high to low
* Salary low to high

Sorting should normally be handled by the backend or database query.

It should not only rearrange the limited jobs currently loaded in the browser.

Salary sorting should handle missing salary values safely.

Jobs without disclosed salaries should not break the query and should normally appear after jobs with valid salary data.

Date sorting should use the best reliable date available, such as:

1. Source date posted
2. Import date
3. Record creation date

The user’s selected sorting option should remain active while changing pages or filters.

## 1.11 Categories and Locations

Jobs should support browsing by:

* Country
* City
* Area
* Category
* Industry
* Job type
* Work mode

Category and location values should be normalised to prevent duplicate or inconsistent options.

Examples of inconsistent values that may require normalisation include:

* KSA
* Saudi
* Saudi Arabia
* Kingdom of Saudi Arabia

The same principle applies to cities, categories, work modes and job types.

## 1.12 Salary Handling

Salary data should be stored in structured fields where available.

Relevant fields include:

* Salary minimum
* Salary maximum
* Salary currency
* Salary period

The system should support:

* Exact salaries
* Salary ranges
* Monthly salaries
* Annual salaries
* Hourly salaries where relevant
* Undisclosed salaries
* Source text preservation where structured parsing is uncertain

The platform must not fabricate salary information.

When salary is unavailable, the frontend should display:

```text
Salary not disclosed
```

Salary filters and sorting should exclude or safely place jobs without valid salary data.

---

# Phase 1 Frontend and User Experience

## 1.13 Page Structure

The platform should have a clear separation between:

* Homepage
* Jobs-results page
* Internal job-detail page

Recommended routes:

```text
/                Homepage
/jobs            Searchable and filterable job results
/jobs/{job_id}   Internal job-detail page
```

The existing framework may use slightly different route formats, but the same separation should be maintained.

## 1.14 Homepage

The homepage should introduce Job Sultan and help users begin a job search.

It should include:

* A prominent job-search interface
* Keyword search
* Country selection
* Optional location or city selection
* A clear Search Jobs button
* A Latest Jobs feed
* Links to browse jobs by country
* Links to popular categories
* Links to remote jobs where available
* A View All Jobs button

The homepage should not display the entire job database.

### Latest Jobs Feed

The homepage should include a limited Latest Jobs section.

It may display approximately 8 to 12 recent jobs.

The Latest Jobs feed should:

* Use the best available posting or import date
* Display useful job-card information
* Link each card to the internal job-detail page
* Include a View All Jobs link
* Avoid loading the full jobs database
* Remain fast on desktop and mobile

## 1.15 Jobs-Results Page

Create a dedicated jobs-results page at a route such as:

```text
/jobs
```

This page should contain the full:

* Search interface
* Filter interface
* Sorting interface
* Active-filter display
* Results count
* Job-card list
* Pagination controls
* Empty state

Searches submitted from the homepage should redirect to this page.

Example:

```text
/jobs?q=accountant&country=uae&sort=newest
```

The complete results interface should live on this page rather than placing the entire job database beneath the homepage search engine.

## 1.16 Job Cards

Each job card should display the information that is genuinely available.

Suggested card information includes:

* Job title
* Company name
* Country
* City or location
* Salary or “Salary not disclosed”
* Job type
* Work mode
* Experience level
* Date posted
* Source
* Short description excerpt where appropriate

Cards should handle missing fields gracefully.

Job cards must be clickable.

Clicking a card should open the internal Job Sultan job-detail page.

It should not immediately send the user away from Job Sultan.

## 1.17 Internal Job-Detail Page

Each job should have an internal page such as:

```text
/jobs/{job_id}
```

The page should display all available saved information, including:

* Full job title
* Company name
* Country
* City
* Area or location
* Salary
* Full description
* Skills
* Category
* Industry
* Job type
* Work mode
* Experience level
* Nationality requirements
* Gender requirements
* Language requirements
* Date posted
* Closing date
* Source

The page should include a prominent Apply button.

The Apply button should use the original external `apply_url`.

The expected user journey is:

```text
Homepage or jobs results
→ Click internal job card
→ Read complete details on Job Sultan
→ Click Apply
→ Continue to original source
```

The external source URL should not replace the internal job-detail page.

## 1.18 Pagination

The jobs-results page should use pagination or another controlled loading method.

It should not load the entire database at once.

Pagination should preserve:

* Search query
* Active filters
* Sorting selection
* Current page
* Results-per-page setting where supported

The backend should perform pagination before returning results.

Suggested pagination information includes:

* Current page
* Total pages
* Total matching jobs
* Previous page
* Next page

## 1.19 Empty, Loading and Error States

The interface should include clear states for:

* Loading jobs
* No matching jobs
* Search errors
* Temporary API failures
* Missing optional information

If no jobs match a search, users should be able to:

* Adjust the query
* Remove individual filters
* Clear all filters
* Return to all jobs

The interface should not display broken blank sections when optional data is unavailable.

## 1.20 Mobile Experience

The complete experience must work on mobile devices.

On smaller screens:

* Search controls should remain usable
* Filters may open in a drawer, sheet or modal
* Active filters should remain visible
* Sorting should remain accessible
* Job cards should remain readable and clickable
* Pagination should remain usable
* The Apply button should remain prominent
* Long descriptions should remain readable
* External links should remain deliberate and clear

## 1.21 Odoo Display and Frontend Integration

Where Odoo remains part of the display layer, imported and normalised jobs should be exposed to it through a stable API or agreed integration method.

Frontend and Odoo display work should not require rewriting the importer for every interface change.

Responsibilities should remain separated:

```text
Importer
→ Database
→ API/backend
→ Frontend or Odoo display
```

## 1.22 Phase 1 Completion Standard

Phase 1 is not complete until the following work together:

### Importing and Data

* Job importing
* Two-stage detail enrichment
* Normalisation
* Duplicate detection
* Existing-record enrichment
* Persistent database storage
* API access
* Salary handling
* Category handling
* Location handling
* All six supported Gulf countries remain supported

### Search and Browsing

* Homepage search
* Dedicated jobs-results page
* Keyword search
* Filters
* Sorting
* Pagination
* Shareable or restorable search state
* Latest Jobs homepage feed

### Job Display

* Useful enriched job cards
* Clickable job cards
* Internal job-detail pages
* Full descriptions
* Clear missing-value handling
* External Apply buttons
* Mobile-compatible display

This phase should result in a stable, usable job board.

---

# Phase 2 — Build the Full Platform Features

After the core job board is stable, add the larger product features.

## 2.1 User System

Add:

* User accounts
* Authentication
* User profiles
* CV and profile information
* Preferred countries
* Preferred cities
* Preferred industries
* Preferred categories
* Preferred job types
* Preferred work modes
* Saved jobs
* Application tracking
* Recently viewed jobs
* User privacy controls
* Account deletion and data-management options

## 2.2 Saved Jobs

Users should be able to:

* Save jobs
* Remove saved jobs
* View saved jobs
* Organise saved jobs where useful
* See whether a job has expired
* Move from a saved job to the internal job-detail page

## 2.3 Application Tracking

Users should be able to record application progress.

Possible states include:

* Interested
* Saved
* Applied
* Interviewing
* Offer received
* Rejected
* Withdrawn
* Closed

The system should not claim that an application was submitted unless the platform can genuinely confirm it.

For external applications, Job Sultan may record that the user clicked Apply, but this should be distinguished from a confirmed completed application.

## 2.4 Job Alerts

Users should be able to select alert preferences.

Preferences may include:

* Keywords
* Countries
* Cities
* Categories
* Industries
* Salary expectations
* Job type
* Work mode
* Experience level
* Alert frequency

The alert system should include:

* Matching engine
* Daily alerts
* Weekly alerts
* Alert management
* Unsubscribe controls
* Duplicate-alert prevention
* Expired-job exclusion

## 2.5 Newsletter System

Add:

* Daily job newsletters
* Weekly job newsletters
* Personalised newsletters
* Country-specific newsletters
* Category-specific newsletters
* Featured jobs
* Editorial job collections
* Employer promotions later
* Subscription management
* Unsubscribe handling
* Delivery monitoring

## 2.6 Automation

Add automated operational processes for:

* Scheduled imports
* Scheduled enrichment
* Stale-job detection
* Automatic deletion or archiving
* Expired-job handling
* Closing-date handling
* Source-health monitoring
* Failed-import retries
* Duplicate cleanup
* Database maintenance
* Newsletter generation
* Alert delivery
* Operational reporting

## 2.7 Admin System

The admin system should support:

* Managing job sources
* Activating and deactivating sources
* Viewing failed imports
* Reviewing imported jobs
* Editing jobs where appropriate
* Archiving jobs
* Viewing source health
* Reviewing duplicate detection
* Managing categories and locations
* Managing user reports
* Viewing analytics
* Managing featured jobs
* Managing newsletters
* Managing alerts
* Reviewing scraper errors

## 2.8 Analytics

Relevant analytics may include:

* Total active jobs
* Jobs by country
* Jobs by category
* Jobs by source
* New jobs per day
* Expired jobs
* Failed imports
* Search queries
* Filter usage
* Job-card clicks
* Job-detail views
* Apply-button clicks
* Saved jobs
* Alert subscriptions
* Newsletter performance

---

# Phase 3 — Improve the Intelligence Layer

After the platform and data are stable, improve the intelligence layer.

## 3.1 AI Job Matching

Use job and user information to improve matching.

Possible inputs include:

* Skills
* Experience
* Preferred country
* Preferred city
* Preferred industry
* Preferred job type
* Preferred work mode
* Salary expectations
* Language ability
* Nationality requirements
* Profile history

Matching results should remain explainable where practical.

## 3.2 CV-to-Job Matching

Allow users to compare their CV or profile against job requirements.

Possible outputs include:

* Match score
* Matching skills
* Missing skills
* Relevant experience
* Location compatibility
* Language compatibility
* Salary compatibility
* Suggested profile improvements

## 3.3 Skill Extraction

Automatically identify skills from:

* Job descriptions
* Requirements
* CVs
* User profiles

Skill values should be normalised to reduce duplicates and spelling variations.

## 3.4 Salary Insights

Possible salary features include:

* Salary ranges by country
* Salary ranges by role
* Salary ranges by industry
* Salary ranges by experience
* Salary comparison
* Disclosed-salary trends
* Currency-normalised insights

Salary insights should clearly distinguish disclosed source data from estimates.

## 3.5 Recommendation Engine

Recommend jobs using:

* User preferences
* Saved jobs
* Viewed jobs
* Previous searches
* Applications
* Skills
* Location
* Experience
* Similar-user behaviour where appropriate

## 3.6 Candidate Ranking

Candidate ranking may be added for employer-facing features later.

Ranking should:

* Use job-relevant information
* Avoid unsupported assumptions
* Be explainable where practical
* Respect privacy
* Avoid unfair or irrelevant criteria
* Keep human review available

---

# Phase 4 — Expand Sources and Infrastructure

After the core platform is stable, expand job-source coverage and technical infrastructure.

## 4.1 Additional Job Sources

Potential sources include:

* Bayt
* GulfTalent
* Naukrigulf
* Company career pages
* Government job portals
* Public APIs
* Employer-submitted jobs
* Recruitment-agency feeds
* Structured XML or RSS feeds

Each source should use a source adapter or clearly isolated extraction logic.

A new source should feed into the same normalised job structure.

## 4.2 Country Expansion

The platform should preserve support for all six intended Gulf countries.

Each country may require:

* Country-specific source URLs
* Country-specific location lists
* Currency handling
* Language handling
* Source availability
* Local job categories
* Local employment conventions

Country-specific source logic must not break shared platform behaviour.

## 4.3 Infrastructure

Later infrastructure may include:

* Google Cloud Run
* Managed database
* Queues
* Background workers
* Scheduled workers
* Object storage
* Caching
* Search indexing
* Centralised logging
* Error monitoring
* Health checks
* Backup systems
* Disaster recovery
* Higher reliability
* Horizontal scaling

## 4.4 Queue and Worker Architecture

Detail-page enrichment, scheduled importing and other long-running tasks should eventually move away from user-facing request routes.

Potential worker responsibilities include:

* Listing-page imports
* Detail-page enrichment
* Retries
* Stale-job checks
* Expired-job archiving
* Job-alert matching
* Newsletter generation
* Search-index updates

## 4.5 Managed Database

The production platform should eventually use a managed persistent database.

The database should support:

* Backups
* Migrations
* Connection security
* Indexing
* Monitoring
* Scaling
* Recovery
* Controlled access

## 4.6 Reliability

The mature platform should provide:

* Source-specific failure isolation
* Automatic retries
* Health monitoring
* Alerting
* Backups
* Rollback capability
* Deployment safety
* Test coverage
* Rate limiting
* Data validation
* Duplicate protection
* Audit logs where required

---

# Development Principles

## Preserve Existing Work

Before making changes:

* Inspect the complete repository
* Identify existing functionality
* Trace frontend and backend connections
* Preserve working six-country logic
* Preserve stable database fields
* Avoid unnecessary rewrites
* Avoid replacing frameworks without a clear reason

## Work in Stages

Large changes should be divided into manageable stages.

Recommended sequence:

1. Audit the repository
2. Verify six-country architecture
3. Complete job-detail enrichment
4. Update existing incomplete jobs
5. Stabilise data normalisation
6. Create internal job-detail pages
7. Make job cards clickable
8. Complete the dedicated jobs-results page
9. Complete filtering
10. Add backend sorting
11. Add pagination
12. Add the homepage Latest Jobs feed
13. Add scheduled imports
14. Expand sources

## Inspect Before Selecting

Scraper selectors must be based on actual returned content.

Do not guess:

* URLs
* CSS selectors
* JSON structures
* Database fields
* Route behaviour
* Frontend behaviour

Inspect first, then implement.

## No Fabricated Data

Never invent:

* Salaries
* Locations
* Company names
* Skills
* Dates
* Requirements

Missing information should be represented honestly.

## Separate Internal Details From External Application

The intended user journey is:

```text
Job Sultan search or homepage
→ Internal Job Sultan job-detail page
→ External source only after clicking Apply
```

## Security

Do not commit:

* API keys
* Database passwords
* Render secrets
* GitHub tokens
* Private credentials
* `.env` files containing secrets

## Change Control

Before substantial implementation:

* Explain the existing architecture
* Identify files that will change
* Explain database implications
* Explain risks
* Run tests
* Show the resulting diff
* Avoid committing, pushing or merging without approval

---

# Master Completion Goal

Job Sultan should become a reliable Gulf-focused job platform that:

* Imports jobs automatically
* Enriches each job automatically
* Supports six Gulf countries
* Normalises multiple sources
* Prevents duplicates
* Displays useful job cards
* Provides a dedicated jobs-results page
* Supports search, filters, sorting and pagination
* Shows Latest Jobs on the homepage
* Provides internal job-detail pages
* Sends users externally only when they choose to apply
* Supports accounts, alerts and newsletters
* Adds AI matching only after the core product is stable
* Expands to additional sources and stronger infrastructure in controlled stages
