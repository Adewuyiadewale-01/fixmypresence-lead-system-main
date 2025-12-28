import asyncio
import re
import urllib.parse
import html
import json
import logging
from typing import List, Optional, Set
import aiohttp
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from common.config import USER_AGENT
from common.google_client import GoogleClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Constants - BALANCED for speed + accuracy
TIMEOUT_SECONDS = 20  # Balanced timeout
CONCURRENT_SITES = 3  # Number of sites to process simultaneously
PLAYWRIGHT_LIMIT = 6  # Balanced: enough for team pages
SUBPAGE_LIMIT = 40    # Balanced: check key pages without crawling everything  
LEVEL2_LIMIT = 30     # Balanced: check profile pages found on team page
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# PASTE YOUR GOOGLE WEB APP URL HERE
# GOOGLE_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbyLmYnBgyBBMGGrDnp15yn-Xf0PC0xNTCuLosWiFpsiKtIhm1_9RL2SqEoNSf7iA6txEA/exec"


RE_LINKEDIN_URL = re.compile(
    # Modified to capture URLs inside text/JS/attributes by looking for the protocol and domain.
    # We relax the ending characters to include quotes/spaces as boundaries for extraction.
    # Also updated to handle country subdomains like nl.linkedin.com
    r'(https?:\/\/(?:[a-z0-9-]+\.)?linkedin\.com\/(?:in|company|showcase|school)\/[a-zA-Z0-9%\-_.\/]+)',
    re.I
)

# Email regex pattern - captures standard email formats
RE_EMAIL = re.compile(
    r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
    re.I
)

# Common generic emails to exclude
EXCLUDED_EMAILS = {
    'example@example.com', 'test@test.com', 'email@example.com',
    'info@example.com', 'noreply@', 'no-reply@', 'mailer-daemon@',
    'postmaster@', 'webmaster@', 'support@example'
}

# Keywords to identify promising internal pages for crawling
INTERNAL_LINK_KEYWORDS = [
    "about", "over", "team", "contact", "people", "wie", "ons", "management", "board", "partner", "human", "medewerker", "regisseur", "adviseur"
]

COMMON_INTERNAL = [
    "/", "/about", "/about-us", "/over", "/over-ons", "/team", "/contact",
    "/contact-us", "/company", "/who-we-are", "/wie-zijn-wij", "/overons",
    "/ons-team", "/social", "/connect", "/footer", "/imprint", "/legal",
    "/team-van", "/onze-mensen", "/organisatie", "/medewerkers"
]


def dedupe_keep_order(urls: List[str]) -> List[str]:
    """Deduplicate while preserving order (critical for deterministic crawling)."""
    seen = set()
    out = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


class LinkedInFinder:
    def __init__(self):
        self.browser = None
        self.playwright = None

    async def start(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)

    async def stop(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    def _clean_url(self, url: str) -> Optional[str]:
        """Validates and normalizes a LinkedIn URL."""
        if not url:
            return None
        
        # Basic cleanup
        url = html.unescape(url).strip()
        
        # Remove trailing slashes
        url = url.rstrip('/')
        # Remove common trailing garbage from bad regex matches or escaped JSON
        url = url.rstrip('\\"')
        url = url.rstrip('"')
        url = url.rstrip("'")

        # Parse and validate domain
        try:
            parsed = urllib.parse.urlparse(url)
            if 'linkedin.com' not in parsed.netloc:
                return None
            
            # Filter out share links and other noise
            path = parsed.path.lower()
            if any(x in path for x in ['/share', '/sharearticle', '/embed', '/pulse/']):
                return None

            # Must contain company or in (profile) identifier
            if not any(x in path for x in ['/company/', '/in/', '/showcase/']):
                # Only if strict validation is desired. 
                # For robust scraping, we generally want company or profile pages.
                return None

            # Clean query params (except maybe originalSubdomain if strictly needed, but usually not)
            # Simplest is to strip all query params for cleaner output
            url = urllib.parse.urlunparse(parsed._replace(query="", fragment=""))
            
            return url
        except Exception:
            return None

    def _extract_from_text(self, text: str) -> Set[str]:
        """Extracts LinkedIn URLs from a raw text string using Regex."""
        found = set()
        matches = RE_LINKEDIN_URL.findall(text)
        for m in matches:
            cleaned = self._clean_url(m)
            if cleaned:
                found.add(cleaned)
        return found

    def _extract_emails_from_text(self, text: str, site_domain: str = "") -> Set[str]:
        """Extracts email addresses from a raw text string using Regex."""
        found = set()
        matches = RE_EMAIL.findall(text)
        for email in matches:
            email = email.lower().strip()
            # Skip excluded generic emails
            if any(excl in email for excl in EXCLUDED_EMAILS):
                continue
            # Skip image/asset extensions that look like emails
            if any(ext in email for ext in ['.png', '.jpg', '.gif', '.svg', '.css', '.js']):
                continue
            found.add(email)
        return found

    def _extract_from_soup(self, soup: BeautifulSoup, base_url: str) -> Set[str]:
        """Extracts LinkedIn URLs from a BeautifulSoup object."""
        found = set()
        
        # 1. Check all <a> tags
        for a in soup.find_all('a', href=True):
            href = a['href']
            full_url = urllib.parse.urljoin(base_url, href)
            if 'linkedin.com' in full_url:
                cleaned = self._clean_url(full_url)
                if cleaned:
                    found.add(cleaned)

        # 2. Check strict social links (often found in footer lists)
        # Look for social icons that might have the link in a parent or unusual attribute
        # (This is partially covered by checking all 'href's, but sometimes data-href is used)
        for tag in soup.find_all(True):
            for attr in ['data-href', 'data-url', 'content']:
                if tag.has_attr(attr):
                    val = tag[attr]
                    if 'linkedin.com' in val:
                         cleaned = self._clean_url(val)
                         if cleaned:
                             found.add(cleaned)
        
        return found

    async def _fetch_static(self, session: aiohttp.ClientSession, url: str) -> tuple[Set[str], Set[str]]:
        """Fetches a single page statically and matches LinkedIn URLs and emails."""
        try:
            # Some sites have SSL issues, so we create a connector that doesn't verify
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            async with session.get(url, headers={"User-Agent": USER_AGENT}, timeout=10, ssl=ssl_context) as response:
                if response.status != 200:
                    return set(), set()
                text = await response.text()
                soup = BeautifulSoup(text, 'html.parser')
                found = self._extract_from_soup(soup, str(response.url))
                # Also try raw regex extraction as fallback
                found.update(self._extract_from_text(text))
                # Extract emails
                emails = self._extract_emails_from_text(text)
                return found, emails
        except Exception as e:
            logger.warning(f"Static fetch failed for {url}: {e}")
            return set(), set()

    async def _process_playwright(self, url: str):
        """Uses Playwright to render the page and find links (handles JS-rendered content)."""
        found = set()
        page = None
        context = None
        try:
            context = await self.browser.new_context(user_agent=USER_AGENT)
            page = await context.new_page()

            await page.route("**/*.{png,jpg,jpeg,gif,css,woff,woff2}", lambda route: route.abort())

            try:
                await page.goto(url, wait_until="networkidle", timeout=TIMEOUT_SECONDS * 1000)
            except PlaywrightTimeoutError:
                logger.warning(f"Timeout loading {url} in Playwright, proceeding with what loaded.")

            # 1) Extract anchors WITH text (so we can detect "Read more"/"Lees meer")
            anchors = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                    href: a.href,
                    text: (a.innerText || a.textContent || '').trim().toLowerCase(),
                    aria: (a.getAttribute('aria-label') || '').trim().toLowerCase(),
                    title: (a.getAttribute('title') || '').trim().toLowerCase()
                }));
            }""")

            base_domain = urllib.parse.urlparse(url).netloc.replace("www.", "")
            internal_links_ordered = []

            cta_words = {"read more", "lees meer", "view profile", "profile", "bio"}

            for a in anchors:
                href = a.get("href") or ""
                if not href:
                    continue

                # LinkedIn direct
                if "linkedin.com" in href:
                    cleaned = self._clean_url(href)
                    if cleaned:
                        found.add(cleaned)
                    continue

                # Internal detection
                try:
                    parsed = urllib.parse.urlparse(href)
                    href_domain = parsed.netloc.replace("www.", "")
                    is_internal = (not parsed.netloc) or (href_domain == base_domain)
                    if not is_internal:
                        continue

                    full_int_url = urllib.parse.urljoin(url, href)

                    # Skip static assets
                    if any(full_int_url.lower().endswith(ext) for ext in (".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".woff", ".woff2", ".gif", ".pdf")):
                        continue

                    # Always keep internal links in DOM order
                    internal_links_ordered.append(full_int_url)

                    # If it's a CTA link, push it again (it will be deduped later but prioritized by order)
                    label = " ".join([a.get("text",""), a.get("aria",""), a.get("title","")]).strip()
                    if label in cta_words:
                        internal_links_ordered.append(full_int_url)

                except Exception:
                    pass

            # 2) Extract from rendered HTML (captures onclick, scripts, templates)
            content = await page.content()
            found.update(self._extract_from_text(content))
            
            # 2b) Extract emails from rendered HTML
            emails = self._extract_emails_from_text(content)

            # 3) Internal links from raw HTML (onclick="location.href='...'" etc.)
            base_clean = base_domain
            re_internal = re.compile(
                r'(https?:\/\/(?:www\.)?' + re.escape(base_clean) + r'\/[a-zA-Z0-9%\-_.\/]+)',
                re.I
            )
            for m in re_internal.findall(content):
                m = m.strip('"\'')
                if any(ext in m.lower() for ext in ['.js', '.css', '.png', '.jpg', '.jpeg', '.svg', '.woff', '.woff2', '.gif', '.pdf']):
                    continue
                internal_links_ordered.append(m)

            # Dedup while preserving order
            internal_links = dedupe_keep_order(internal_links_ordered)

            return found, internal_links, emails

        except Exception as e:
            logger.error(f"Playwright error for {url}: {e}")
            return found, [], set()
        finally:
            if page:
                await page.close()
            if context:
                await context.close()

    async def find_linkedin_links(self, start_url: str) -> tuple[List[str], List[str]]:
        """
        Main method to find LinkedIn links AND emails for a given website.
        Returns: (linkedin_urls, emails)
        """
        if not start_url.startswith('http'):
            start_url = 'https://' + start_url

        all_found = set()
        all_emails = set()
        
        # Strategy 1: Playwright on Homepage (Most robust for single-page apps / JS-heavy sites)
        logger.info(f"Checking {start_url} (Playwright)...")
        # Ensure we unpack all return values (linkedin, internal_links, emails)
        results, discovered_internal_links, homepage_emails = await self._process_playwright(start_url)
        all_found.update(results)
        all_emails.update(homepage_emails)

        if all_found:
            logger.info(f"Found on homepage: {results}")

        # Strategy 2: Check promising subpages
        # Combine hardcoded COMMON_INTERNAL with discovered links
        
        parsed = urllib.parse.urlparse(start_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        
        # Post-process subpages: prioritize "high value" pages for Playwright
        high_value_keywords = ["team", "mensen", "people", "wie", "ons", "contact", "about", "regisseur", "adviseur"]
        
        # Score function for sorting
        def score_url(u):
            score = 0
            if any(k in u.lower() for k in high_value_keywords):
                score += 2
            if discovered_internal_links and u in discovered_internal_links:
                score += 1
            return score

        # Build ordered candidate list: common paths first, then discovered links (DOM order)
        subpages_ordered = [urllib.parse.urljoin(base, path) for path in COMMON_INTERNAL]
        if discovered_internal_links:
            logger.info(f"Discovered {len(discovered_internal_links)} internal links from homepage to crawl.")
            subpages_ordered.extend(discovered_internal_links)

        # Remove homepage + dedupe, keep order
        subpages_ordered = [u for u in subpages_ordered if u and u.rstrip("/") != start_url.rstrip("/")]
        subpages_ordered = dedupe_keep_order(subpages_ordered)

        # Score + sort deterministically (use url as tie-breaker)
        sorted_subs = sorted(subpages_ordered, key=lambda u: (score_url(u), u), reverse=True)

        final_list = sorted_subs[:SUBPAGE_LIMIT]
        
        logger.info(f"Checking {len(final_list)} subpages...")

        # We will use Playwright for the top N most likely "Team" pages, and static for the rest
        # because Playwright is expensive.
        pw_count = 0
        
        async with aiohttp.ClientSession() as session:
            tasks = []
            for url in final_list:
                # Re-calculate high value for logic below (or use score, but bool is enough)
                is_high_value = any(k in url.lower() for k in high_value_keywords)
                
                # If high value and we haven't hit limit, use Playwright
                # (Reuse the existing browser instance)
                if is_high_value and pw_count < PLAYWRIGHT_LIMIT:
                    pw_count += 1
                    logger.info(f"  -> Deep check (Playwright): {url}")
                    
                    # Run Playwright and capture NEW internal links found on this subpage
                    res, new_links, page_emails = await self._process_playwright(url)
                    all_found.update(res)
                    all_emails.update(page_emails)
                    
                    # If this was a Team page, it usually links to individual profiles.
                    # We should check those new links too (Level 2 crawl).
                    # Heuristic: links that are internal and child of current path OR look like profile
                    # But simpler: just check all new unique internal links found on this Team page
                    # We accept a small performance penalty for robustness.
                    
                    # Filter: don't re-check pages we already listed in `final_list` or `start_url`
                    # We verify them statically to be fast, unless we have Playwright budget left?
                    # Let's verify them statically.
                    
                    level2_links = [l for l in new_links if l not in final_list and l != start_url and l.strip('/') != start_url.strip('/')]
                    level2_links = dedupe_keep_order(level2_links)[:LEVEL2_LIMIT]
                    
                    if level2_links:
                         logger.info(f"    -> Found {len(level2_links)} potential profile links on {url}. Checking statically...")
                         l2_tasks = [self._fetch_static(session, l2) for l2 in level2_links]
                         l2_results = await asyncio.gather(*l2_tasks)
                         for res2, emails2 in l2_results:
                                all_found.update(res2)
                                all_emails.update(emails2)
                    
                else:
                    # Static check
                    tasks.append(self._fetch_static(session, url))

            if tasks:
                results_list = await asyncio.gather(*tasks)
                for res, emails in results_list:
                    all_found.update(res)
                    all_emails.update(emails)

        # Post-processing filtration
        # The prompt asks to "scrape every linkedin links", so we return all.
        
        return sorted(list(all_found)), sorted(list(all_emails))

async def upload_to_google(session: aiohttp.ClientSession, website: str, links: List[str], emails: List[str], 
                           tab: str = None, row: int = None):
    """Sends the scraped data to the Google Apps Script Webhook."""
    if not links and not emails:
        logger.info(f"Skipping upload for {website}: No links or emails found.")
        return

    payload = {
        "input_website": website,
        "linkedin_urls": links,
        "emails": emails
    }
    
    # Add sheet metadata if present (for updating status column)
    if tab and row:
        payload["tab"] = tab
        payload["row"] = row
    
    # Use unified client
    await GoogleClient.post_async(session, payload)



async def process_single_site(finder: LinkedInFinder, google_session: aiohttp.ClientSession, 
                               site_info: dict, semaphore: asyncio.Semaphore) -> dict:
    """Process a single site with semaphore-controlled concurrency.
    
    site_info can be:
    - A simple string URL
    - A dict with {url, tab, row} for Google Sheets integration
    """
    async with semaphore:
        # Handle both string URLs and dict format
        if isinstance(site_info, str):
            url = site_info
            tab = None
            row = None
        else:
            url = site_info.get("url", "")
            tab = site_info.get("tab")
            row = site_info.get("row")
        
        try:
            logger.info(f"🚀 Starting: {url}")
            links, emails = await finder.find_linkedin_links(url)
            
            logger.info(f"✅ {url} -> {len(links)} LinkedIn, {len(emails)} emails")
            
            # Build result object
            result = {
                "input_website": url,
                "linkedin_urls": links,
                "emails": emails
            }
            
            # Add sheet metadata if present
            if tab and row:
                result["tab"] = tab
                result["row"] = row
            
            # Upload to Google (includes sheet status update if tab/row provided)
            await upload_to_google(google_session, url, links, emails, tab, row)
            
            return result
        except Exception as e:
            logger.error(f"❌ Failed {url}: {e}")
            return {
                "input_website": url,
                "linkedin_urls": [],
                "emails": [],
                "error": str(e)
            }


async def fetch_urls_from_sheet(session: aiohttp.ClientSession) -> List[dict]:
    """Fetch unprocessed URLs from Google Sheets via Apps Script."""
    return await GoogleClient.fetch_urls(session)



async def main_from_sheet(batch_size: int = 50):
    """Main function that fetches URLs from Google Sheets and processes them."""
    import time
    start_time = time.time()
    
    logger.info(f"🔥 Fetching URLs from Google Sheets...")
    
    async with aiohttp.ClientSession() as session:
        # Fetch URLs from sheet
        url_list = await fetch_urls_from_sheet(session)
        
        if not url_list:
            logger.info("No unprocessed URLs found in sheets.")
            return
        
        # Limit to batch_size
        if len(url_list) > batch_size:
            logger.info(f"📦 Processing first {batch_size} of {len(url_list)} URLs")
            url_list = url_list[:batch_size]
        
        logger.info(f"🔥 Starting batch processing of {len(url_list)} sites (concurrency: {CONCURRENT_SITES})")
        
        finder = LinkedInFinder()
        await finder.start()
        
        semaphore = asyncio.Semaphore(CONCURRENT_SITES)
        
        try:
            tasks = [
                process_single_site(finder, session, site_info, semaphore)
                for site_info in url_list
            ]
            all_results = await asyncio.gather(*tasks)
        finally:
            await finder.stop()
        
        # Save results
        with open("linkedin_results.json", "w") as f:
            json.dump(all_results, f, indent=2)
        
        elapsed = time.time() - start_time
        successful = sum(1 for r in all_results if not r.get("error"))
        total_linkedin = sum(len(r.get("linkedin_urls", [])) for r in all_results)
        total_emails = sum(len(r.get("emails", [])) for r in all_results)
        
        logger.info(f"")
        logger.info(f"{'='*60}")
        logger.info(f"📊 BATCH COMPLETE")
        logger.info(f"{'='*60}")
        logger.info(f"   Sites processed: {successful}/{len(url_list)}")
        logger.info(f"   LinkedIn URLs found: {total_linkedin}")
        logger.info(f"   Emails found: {total_emails}")
        logger.info(f"   Time elapsed: {elapsed:.1f}s ({elapsed/len(url_list):.1f}s per site)")
        logger.info(f"{'='*60}")
        logger.info(f"Results saved to linkedin_results.json")


async def main(urls: List[str]):
    """Main function with parallel site processing (for manual URL list)."""
    import time
    start_time = time.time()
    
    logger.info(f"🔥 Starting batch processing of {len(urls)} sites (concurrency: {CONCURRENT_SITES})")
    
    finder = LinkedInFinder()
    await finder.start()
    
    semaphore = asyncio.Semaphore(CONCURRENT_SITES)
    
    async with aiohttp.ClientSession() as google_session:
        try:
            tasks = [
                process_single_site(finder, google_session, url, semaphore)
                for url in urls
            ]
            all_results = await asyncio.gather(*tasks)
        finally:
            await finder.stop()
    
    with open("linkedin_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    
    elapsed = time.time() - start_time
    successful = sum(1 for r in all_results if not r.get("error"))
    total_linkedin = sum(len(r.get("linkedin_urls", [])) for r in all_results)
    total_emails = sum(len(r.get("emails", [])) for r in all_results)
    
    logger.info(f"")
    logger.info(f"{'='*60}")
    logger.info(f"📊 BATCH COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"   Sites processed: {successful}/{len(urls)}")
    logger.info(f"   LinkedIn URLs found: {total_linkedin}")
    logger.info(f"   Emails found: {total_emails}")
    logger.info(f"   Time elapsed: {elapsed:.1f}s ({elapsed/len(urls):.1f}s per site)")
    logger.info(f"{'='*60}")
    logger.info(f"Results saved to linkedin_results.json")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--sheet":
        # Fetch from Google Sheets
        batch_size = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        asyncio.run(main_from_sheet(batch_size))
    else:
        # Manual test URLs
        test_urls = [
            "https://www.assetpartners.nl/",
            "https://www.famadvocaten.nl/nl/",
            "https://www.axento.nl/"
        ]
        asyncio.run(main(test_urls))


