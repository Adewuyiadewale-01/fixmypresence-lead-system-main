# Shared Configuration for Fixmypresence System

# Webhook integration (Google Apps Script)
# Using the URL found in the enricher as it appears to be the live one.
WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbyLmYnBgyBBMGGrDnp15yn-Xf0PC0xNTCuLosWiFpsiKtIhm1_9RL2SqEoNSf7iA6txEA/exec"
WEBHOOK_SECRET = "FMP-Scraper"
WEBHOOK_SPREADSHEET_ID = "1KrKoeun-h6eEzSK6-cc4_MUHriuabg8GyAR9b8NR7fc"
WEBHOOK_TAB_NAME = "Leads"

# Source sheet webhook (Alpha/Beta/Gamma tabs)
# Deploy source_apps_script.js on sheet 1YF8WvLTPu... and paste the URL here.
# Handles: GET (fetch URLs), POST action=mark_processed (update Scrape Status)
SOURCE_WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbwFHK6yToabfEPucGMVtGKtqwEIG0lWokrZLDn-J23aUahGq0-_sn5xo5rgw15AEHH79g/exec"

# Output sheet webhook (Employee_db tab)
# Deploy output_apps_script.js on the output sheet and paste the URL here.
# Handles: POST companies=[...] (write grouped rows to Employee_db)
OUTPUT_WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbyYNaJdNvpVItavLbn100lzvMJ_fLdDTISFOgKWOBmfT2IATZmxqkhYOCbeFxO8xMis8A/exec"

# User Agent
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Common Aggregator Domains to ignore
AGGREGATOR_DOMAINS = {
    "google.com",
    "maps.google.com",
    "momentumfo.com",
    "yellowpages.com",
    "yelp.com",
    "foursquare.com",
    "facebook.com",
    "linkedin.com",
    "instagram.com",
    "twitter.com",
    "tiktok.com",
    "pinterest.com",
    "detelefoongids.nl",
    "bedrijveninformatie.nl",
    "kvk.nl",
    "glassdoor.com",
    "indeed.com",
    "trulylocal.com",
    "tripadvisor.com",
    "goudenpagina.nl",
}
