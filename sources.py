SOURCES = [

    {
    "name": "WUZZUF",
    "type": "html",
    "url": "https://wuzzuf.net/saudi/a/jobs-in-saudi-arabia",
    "active": True,
    "country": "Saudi Arabia",
    "language": "English",
    "listing_parser": "wuzzuf",
    "detail_parser": "wuzzuf",
    "timeout": 45,
    "polite_delay": 1.0,
    "max_retries": 2,
    "retry_delay": 1.0
},

{
    "name": "Bayt",
    "type": "html",
    "url": "https://www.bayt.com/en/saudi-arabia/jobs/",
    "active": False,
    "country": "Saudi Arabia",
    "language": "English",
    "timeout": 45
},

{
    "name": "GulfTalent",
    "type": "html",
    "url": "https://www.gulftalent.com/saudi-arabia/jobs",
    "active": False,
    "country": "Saudi Arabia",
    "language": "English",
    "timeout": 45
},

{
    "name": "Naukrigulf",
    "type": "html",
    "url": "https://www.naukrigulf.com/jobs-in-saudi-arabia",
    "active": False,
    "country": "Saudi Arabia",
    "language": "English",
    "timeout": 45
},

{
    "name": "Lever - Flow",
    "type": "json",
    "url": "https://api.lever.co/v0/postings/flowlife?mode=json",
    "active": True,
    "provider": "lever",
    "site": "flowlife",
    "company_name": "Flow",
    "timeout": 45
},

{
    "name": "Lever - Trendyol",
    "type": "json",
    "url": "https://api.lever.co/v0/postings/trendyol?mode=json",
    "active": True,
    "provider": "lever",
    "site": "trendyol",
    "company_name": "Trendyol",
    "timeout": 45
},

{
    "name": "Lever - Contentsquare",
    "type": "json",
    "url": "https://api.lever.co/v0/postings/contentsquare?mode=json",
    "active": False,
    "provider": "lever",
    "site": "contentsquare",
    "company_name": "Contentsquare",
    "timeout": 45
}

]
