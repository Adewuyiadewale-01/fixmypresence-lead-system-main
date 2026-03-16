import asyncio
import hashlib
import re
import time
import urllib.parse
import html
import json
import logging
from typing import List, Optional, Set, Dict
from datetime import datetime
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

EXCLUDED_EMAIL_DOMAINS = {
    'sentry.io',
    'sentry-next.wixpress.com',
}

RE_MACHINE_EMAIL_LOCAL = re.compile(r'^[a-f0-9]{12,}$', re.I)

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
            local_part, _, domain = email.partition('@')
            # Skip excluded generic emails
            if any(excl in email for excl in EXCLUDED_EMAILS):
                continue
            # Skip telemetry / error-reporting inboxes and hashed machine addresses
            if domain in EXCLUDED_EMAIL_DOMAINS or domain.endswith('.wixpress.com'):
                continue
            if RE_MACHINE_EMAIL_LOCAL.fullmatch(local_part):
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

    async def find_linkedin_links(self, start_url: str) -> tuple[List[str], List[str], Optional[BeautifulSoup]]:
        """
        Main method to find LinkedIn links AND emails for a given website.
        Returns: (linkedin_urls, emails, homepage_soup)
        """
        if not start_url.startswith('http'):
            start_url = 'https://' + start_url

        all_found = set()
        all_emails = set()
        homepage_soup = None

        # Strategy 1: Playwright on Homepage (Most robust for single-page apps / JS-heavy sites)
        logger.info(f"Checking {start_url} (Playwright)...")
        # Ensure we unpack all return values (linkedin, internal_links, emails)
        results, discovered_internal_links, homepage_emails = await self._process_playwright(start_url)
        all_found.update(results)
        all_emails.update(homepage_emails)

        # Fetch homepage HTML for person extraction (static fetch for soup)
        try:
            import ssl
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            async with aiohttp.ClientSession() as tmp_session:
                async with tmp_session.get(start_url, headers={"User-Agent": USER_AGENT},
                                           timeout=aiohttp.ClientTimeout(total=15), ssl=ssl_ctx) as resp:
                    if resp.status == 200:
                        homepage_soup = BeautifulSoup(await resp.text(), 'html.parser')
        except Exception:
            pass

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

        return sorted(list(all_found)), sorted(list(all_emails)), homepage_soup

def generate_company_id(company_name: str) -> str:
    """Generate deterministic company ID: YYYYMMDD-<6-char-hash>."""
    date_prefix = datetime.now().strftime("%Y%m%d")
    hash_suffix = hashlib.md5(company_name.lower().strip().encode()).hexdigest()[:6]
    return f"{date_prefix}-{hash_suffix}"


class PersonExtractor:
    """Extracts person data (name, role, email, linkedin) using 5 fallback strategies."""

    def __init__(self):
        self._ssl_context = None

    def _get_ssl_context(self):
        if not self._ssl_context:
            import ssl
            self._ssl_context = ssl.create_default_context()
            self._ssl_context.check_hostname = False
            self._ssl_context.verify_mode = ssl.CERT_NONE
        return self._ssl_context

    def strategy1_dom_context(self, soup: BeautifulSoup, linkedin_urls: List[str]) -> List[Dict]:
        """Strategy 1: DOM context mapping - walk up from /in/ links to find nearby name + role."""
        persons = []
        seen_urls = set()

        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            if '/in/' not in href or 'linkedin.com' not in href:
                continue

            # Normalize
            clean_href = href.rstrip('/').split('?')[0]
            if clean_href in seen_urls:
                continue
            seen_urls.add(clean_href)

            name = ""
            role = ""

            # The link text itself often contains the person's name
            link_text = a_tag.get_text(strip=True)
            if link_text and len(link_text) > 2 and not link_text.startswith('http'):
                name = link_text

            # Walk up DOM to find a containing card/section
            parent = a_tag.parent
            for _ in range(5):  # Walk up max 5 levels
                if parent is None:
                    break

                # Look for text nodes that might be name or role
                texts = [t.strip() for t in parent.stripped_strings if len(t.strip()) > 1]

                if not name:
                    # First substantial text that isn't the URL is likely the name
                    for t in texts:
                        if 'linkedin.com' not in t.lower() and len(t) < 80:
                            name = t
                            break

                if not role and len(texts) > 1:
                    # Role is typically the second text element in a person card
                    for t in texts[1:]:
                        if ('linkedin.com' not in t.lower() and t != name
                                and len(t) < 120):
                            role = t
                            break

                if name and role:
                    break
                parent = parent.parent

            if name:
                persons.append({
                    "name": name,
                    "role": role,
                    "linkedin": clean_href,
                    "email": "",
                    "confidence": "high",
                    "strategy": "dom_context"
                })

        return persons

    def strategy2_json_ld(self, soup: BeautifulSoup) -> List[Dict]:
        """Strategy 2: JSON-LD structured data - parse Person schema."""
        persons = []

        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string or '{}')
                items = data if isinstance(data, list) else [data]
                for item in items:
                    self._extract_jsonld_persons(item, persons)
            except (json.JSONDecodeError, TypeError):
                continue

        return persons

    def _extract_jsonld_persons(self, item: dict, persons: List[Dict]):
        """Recursively extract Person entries from JSON-LD data."""
        if not isinstance(item, dict):
            return

        item_type = item.get('@type', '')
        if isinstance(item_type, list):
            item_type = ' '.join(item_type)

        if 'Person' in item_type:
            name = item.get('name', '')
            role = item.get('jobTitle', '') or item.get('description', '')
            linkedin = ''
            email = item.get('email', '')

            # Check sameAs for LinkedIn URL
            same_as = item.get('sameAs', [])
            if isinstance(same_as, str):
                same_as = [same_as]
            for url in same_as:
                if 'linkedin.com' in url:
                    linkedin = url
                    break

            if name:
                persons.append({
                    "name": name,
                    "role": role,
                    "linkedin": linkedin,
                    "email": email.replace('mailto:', '') if email else '',
                    "confidence": "high",
                    "strategy": "json_ld"
                })

        # Check nested structures (e.g., Organization.employee)
        for key in ('employee', 'member', 'employees', 'founder', 'alumni'):
            nested = item.get(key, [])
            if isinstance(nested, dict):
                nested = [nested]
            if isinstance(nested, list):
                for sub in nested:
                    self._extract_jsonld_persons(sub, persons)

    async def strategy3_linkedin_page(self, session: aiohttp.ClientSession,
                                       linkedin_urls: List[str]) -> List[Dict]:
        """Strategy 3: Fetch LinkedIn /in/ page, extract title and og:title meta."""
        persons = []
        profile_urls = [u for u in linkedin_urls if '/in/' in u]

        for url in profile_urls[:10]:  # Limit to avoid rate limiting
            try:
                headers = {"User-Agent": USER_AGENT}
                async with session.get(url, headers=headers, timeout=10,
                                       ssl=self._get_ssl_context(),
                                       allow_redirects=True) as resp:
                    if resp.status != 200:
                        continue
                    text = await resp.text()
                    li_soup = BeautifulSoup(text, 'html.parser')

                    name = ""
                    role = ""

                    # Try og:title first (usually "Name - Role - LinkedIn")
                    og_title = li_soup.find('meta', property='og:title')
                    if og_title and og_title.get('content'):
                        parts = og_title['content'].split(' - ')
                        if parts:
                            name = parts[0].strip()
                        if len(parts) > 1:
                            role = parts[1].strip()

                    # Fallback to <title>
                    if not name:
                        title_tag = li_soup.find('title')
                        if title_tag and title_tag.string:
                            parts = title_tag.string.split(' - ')
                            if parts:
                                name = parts[0].strip()
                            if len(parts) > 1:
                                role = parts[1].strip()

                    # Clean up LinkedIn-appended suffixes
                    if name and '| LinkedIn' in name:
                        name = name.replace('| LinkedIn', '').strip()
                    if role and '| LinkedIn' in role:
                        role = role.replace('| LinkedIn', '').strip()

                    if name and name.lower() != 'linkedin':
                        persons.append({
                            "name": name,
                            "role": role,
                            "linkedin": url,
                            "email": "",
                            "confidence": "medium",
                            "strategy": "linkedin_page"
                        })
            except Exception as e:
                logger.debug(f"Strategy 3 failed for {url}: {e}")
                continue

            # Small delay to avoid LinkedIn rate limits
            await asyncio.sleep(0.5)

        return persons

    def strategy4_email_parsing(self, emails: List[str], site_domain: str = "") -> List[Dict]:
        """Strategy 4: Parse emails like john.doe@co.com -> 'John Doe'."""
        persons = []

        for email in emails:
            local_part = email.split('@')[0]
            # Skip generic addresses
            generic = {'info', 'contact', 'hello', 'admin', 'support', 'office',
                       'sales', 'help', 'mail', 'team', 'hr', 'jobs', 'career',
                       'billing', 'noreply', 'no-reply', 'webmaster', 'postmaster'}
            if local_part.lower() in generic:
                continue
            if RE_MACHINE_EMAIL_LOCAL.fullmatch(local_part):
                continue

            # Try to parse name from local part (john.doe, john_doe, johndoe)
            parts = re.split(r'[._\-]', local_part)
            if len(parts) >= 2:
                name_parts = [p.capitalize() for p in parts if re.fullmatch(r'[a-zA-Z]{2,}', p)]
                if len(name_parts) < 2:
                    continue
                name = ' '.join(name_parts)
            elif re.fullmatch(r'[a-zA-Z]{4,}', local_part):
                # Single-word local part - capitalize but mark low confidence
                name = local_part.capitalize()
            else:
                continue

            persons.append({
                "name": name,
                "role": "",
                "linkedin": "",
                "email": email,
                "confidence": "medium",
                "strategy": "email_parsing"
            })

        return persons

    def strategy5_url_slug(self, linkedin_urls: List[str]) -> List[Dict]:
        """Strategy 5: Parse /in/john-doe-123 -> 'John Doe'."""
        persons = []
        seen = set()

        for url in linkedin_urls:
            if '/in/' not in url:
                continue

            # Extract slug: /in/john-doe-123abc
            match = re.search(r'/in/([a-zA-Z0-9\-]+)', url)
            if not match:
                continue

            slug = match.group(1)
            if slug in seen:
                continue
            seen.add(slug)

            # Remove trailing ID numbers (e.g., john-doe-123abc -> john-doe)
            # LinkedIn slugs often end with random alphanumeric suffixes
            parts = slug.split('-')
            # Remove last part if it looks like a hash/id (contains digits)
            name_parts = []
            for p in parts:
                if re.match(r'^[a-zA-Z]+$', p) and len(p) > 1:
                    name_parts.append(p.capitalize())
                elif re.match(r'^[a-zA-Z]+$', p) and len(p) == 1:
                    # Single letter, could be middle initial
                    name_parts.append(p.upper())
                # Stop at first numeric/mixed part (likely ID suffix)
                elif re.search(r'\d', p):
                    break

            if len(name_parts) >= 2:
                name = ' '.join(name_parts)
                persons.append({
                    "name": name,
                    "role": "",
                    "linkedin": url,
                    "email": "",
                    "confidence": "low",
                    "strategy": "url_slug"
                })

        return persons

    async def extract_persons(self, session: aiohttp.ClientSession,
                               soup: BeautifulSoup,
                               linkedin_urls: List[str],
                               emails: List[str],
                               site_domain: str = "") -> List[Dict]:
        """Run all 5 strategies and merge/deduplicate results."""
        all_persons = []

        # Strategy 1: DOM context mapping (high confidence)
        s1 = self.strategy1_dom_context(soup, linkedin_urls)
        all_persons.extend(s1)
        logger.info(f"  Strategy 1 (DOM context): {len(s1)} persons")

        # Strategy 2: JSON-LD structured data (high confidence)
        s2 = self.strategy2_json_ld(soup)
        all_persons.extend(s2)
        logger.info(f"  Strategy 2 (JSON-LD): {len(s2)} persons")

        # Strategy 3: LinkedIn page scrape (medium confidence)
        # Only for /in/ URLs not already found by strategy 1/2
        found_linkedin_urls = {p['linkedin'] for p in all_persons if p.get('linkedin')}
        remaining_urls = [u for u in linkedin_urls if u not in found_linkedin_urls and '/in/' in u]
        if remaining_urls:
            s3 = await self.strategy3_linkedin_page(session, remaining_urls)
            all_persons.extend(s3)
            logger.info(f"  Strategy 3 (LinkedIn page): {len(s3)} persons")

        # Strategy 4: Email parsing (medium confidence)
        s4 = self.strategy4_email_parsing(emails, site_domain)
        all_persons.extend(s4)
        logger.info(f"  Strategy 4 (Email parsing): {len(s4)} persons")

        # Strategy 5: URL slug parsing (low confidence, fallback only)
        found_linkedin_urls = {p['linkedin'] for p in all_persons if p.get('linkedin')}
        remaining_urls = [u for u in linkedin_urls if u not in found_linkedin_urls and '/in/' in u]
        if remaining_urls:
            s5 = self.strategy5_url_slug(remaining_urls)
            all_persons.extend(s5)
            logger.info(f"  Strategy 5 (URL slug): {len(s5)} persons")

        # Deduplicate: prefer higher confidence entries
        return self._dedupe_persons(all_persons)

    def _dedupe_persons(self, persons: List[Dict]) -> List[Dict]:
        """Deduplicate persons, preferring higher-confidence entries."""
        confidence_rank = {"high": 3, "medium": 2, "low": 1}
        best = {}  # key -> person dict

        for p in persons:
            # Build dedup key from linkedin URL or name
            key = p.get('linkedin', '').rstrip('/').lower()
            if not key or key == '':
                key = p.get('name', '').lower().strip()
            if not key:
                continue

            existing = best.get(key)
            if not existing:
                best[key] = p
            else:
                # Keep the one with higher confidence
                if confidence_rank.get(p.get('confidence', 'low'), 0) > \
                   confidence_rank.get(existing.get('confidence', 'low'), 0):
                    best[key] = p
                else:
                    # Merge missing fields from lower-confidence source
                    for field in ('name', 'role', 'email', 'linkedin'):
                        if not existing.get(field) and p.get(field):
                            existing[field] = p[field]

        return list(best.values())


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
                               site_info: dict, semaphore: asyncio.Semaphore,
                               person_extractor: PersonExtractor = None) -> dict:
    """Process a single site with semaphore-controlled concurrency.

    site_info can be:
    - A simple string URL
    - A dict with {url/website, name, tab, row, linkedin, employees, people_link}
    """
    async with semaphore:
        # Handle both string URLs and dict format
        if isinstance(site_info, str):
            url = site_info
            name = ""
            tab = None
            row = None
        else:
            url = site_info.get("url") or site_info.get("website", "")
            name = site_info.get("name", "")
            tab = site_info.get("tab")
            row = site_info.get("row")

        try:
            logger.info(f"Starting: {url}")
            links, emails, soup = await finder.find_linkedin_links(url)

            logger.info(f"Done {url} -> {len(links)} LinkedIn, {len(emails)} emails")

            # Extract person data if extractor is provided
            persons = []
            if person_extractor and (links or emails):
                persons = await person_extractor.extract_persons(
                    google_session, soup, links, emails,
                    site_domain=urllib.parse.urlparse(url).netloc
                )
                logger.info(f"  Extracted {len(persons)} persons from {url}")

            # Find company LinkedIn URL (first /company/ URL)
            company_linkedin = ""
            for li_url in links:
                if '/company/' in li_url:
                    company_linkedin = li_url
                    break

            # Build result object
            result = {
                "input_website": url,
                "name": name,
                "linkedin_urls": links,
                "company_linkedin": company_linkedin,
                "emails": emails,
                "persons": persons,
            }

            # Add sheet metadata if present
            if tab and row:
                result["tab"] = tab
                result["row"] = row

            # Legacy upload (for non-source-sheet usage)
            if not tab:
                await upload_to_google(google_session, url, links, emails, tab, row)

            return result
        except Exception as e:
            logger.error(f"Failed {url}: {e}")
            return {
                "input_website": url,
                "name": name if not isinstance(site_info, str) else "",
                "linkedin_urls": [],
                "company_linkedin": "",
                "emails": [],
                "persons": [],
                "error": str(e)
            }


async def fetch_urls_from_sheet(session: aiohttp.ClientSession) -> List[dict]:
    """Fetch unprocessed URLs from Google Sheets via Apps Script."""
    return await GoogleClient.fetch_urls(session)



async def main_from_sheet(batch_size: int = 50, dry_run: bool = False):
    """Main function that fetches URLs from source sheet (Alpha/Beta/Gamma) and processes them.

    Flow:
    1. GET source webhook -> receive company list from Alpha/Beta/Gamma
    2. For each company: scrape website -> find company LinkedIn + person data
    3. Generate unique company ID (YYYYMMDD-<hash>)
    4. POST grouped results to output webhook -> Employee_db
    5. Save local linkedin_results.json backup
    """
    start_time = time.time()

    logger.info("Fetching URLs from source sheet (Alpha/Beta/Gamma)...")

    async with aiohttp.ClientSession() as session:
        # Step 1: Fetch from source sheet
        url_list = await GoogleClient.fetch_source_urls(session)

        if not url_list:
            # Fallback to legacy endpoint
            logger.info("No URLs from source sheet, trying legacy endpoint...")
            url_list = await fetch_urls_from_sheet(session)

        if not url_list:
            logger.info("No unprocessed URLs found.")
            return

        # Limit to batch_size
        if len(url_list) > batch_size:
            logger.info(f"Processing first {batch_size} of {len(url_list)} URLs")
            url_list = url_list[:batch_size]

        logger.info(f"Starting batch processing of {len(url_list)} sites (concurrency: {CONCURRENT_SITES})")

        if dry_run:
            logger.info("DRY RUN: will scrape but NOT write to output sheet")

        finder = LinkedInFinder()
        await finder.start()
        person_extractor = PersonExtractor()
        semaphore = asyncio.Semaphore(CONCURRENT_SITES)

        try:
            tasks = [
                process_single_site(finder, session, site_info, semaphore, person_extractor)
                for site_info in url_list
            ]
            all_results = await asyncio.gather(*tasks)
        finally:
            await finder.stop()

        # Step 3 & 4: Build grouped output and POST to Employee_db
        companies_payload = []
        source_updates = []

        for result in all_results:
            if result.get("error"):
                # Mark as failed in source sheet
                if result.get("tab") and result.get("row"):
                    source_updates.append({
                        "tab": result["tab"],
                        "row": result["row"],
                        "status": "error"
                    })
                continue

            company_name = result.get("name") or urllib.parse.urlparse(result["input_website"]).netloc
            company_id = generate_company_id(company_name)

            company_entry = {
                "id": company_id,
                "name": company_name,
                "website": result["input_website"],
                "linkedin": result.get("company_linkedin", ""),
                "class": result.get("tab", ""),
                "persons": [
                    {
                        "name": p.get("name", ""),
                        "role": p.get("role", ""),
                        "email": p.get("email", ""),
                        "linkedin": p.get("linkedin", ""),
                    }
                    for p in result.get("persons", [])
                ]
            }
            companies_payload.append(company_entry)

            # Mark as done in source sheet
            if result.get("tab") and result.get("row"):
                source_updates.append({
                    "tab": result["tab"],
                    "row": result["row"],
                    "status": "done"
                })

        if not dry_run:
            # POST grouped results to output sheet
            if companies_payload:
                success = await GoogleClient.post_to_output(session, companies_payload)
                if success:
                    logger.info(f"Posted {len(companies_payload)} companies to Employee_db")
                else:
                    logger.error("Failed to post to Employee_db")

            # Mark rows as processed in source sheet
            if source_updates:
                await GoogleClient.mark_source_processed(session, source_updates)
                logger.info(f"Marked {len(source_updates)} rows as processed in source sheet")
        else:
            logger.info(f"DRY RUN: Would post {len(companies_payload)} companies to Employee_db")

        # Step 5: Save local backup
        import os
        output_dir = os.path.dirname(os.path.abspath(__file__))
        output_path = os.path.join(output_dir, "linkedin_results.json")
        with open(output_path, "w") as f:
            json.dump(all_results, f, indent=2, default=str)

        elapsed = time.time() - start_time
        successful = sum(1 for r in all_results if not r.get("error"))
        total_linkedin = sum(len(r.get("linkedin_urls", [])) for r in all_results)
        total_emails = sum(len(r.get("emails", [])) for r in all_results)
        total_persons = sum(len(r.get("persons", [])) for r in all_results)

        logger.info("")
        logger.info(f"{'='*60}")
        logger.info(f"BATCH COMPLETE")
        logger.info(f"{'='*60}")
        logger.info(f"   Sites processed: {successful}/{len(url_list)}")
        logger.info(f"   LinkedIn URLs found: {total_linkedin}")
        logger.info(f"   Emails found: {total_emails}")
        logger.info(f"   Persons extracted: {total_persons}")
        logger.info(f"   Companies posted: {len(companies_payload)}")
        logger.info(f"   Time elapsed: {elapsed:.1f}s ({elapsed/len(url_list):.1f}s per site)")
        logger.info(f"{'='*60}")
        logger.info(f"Results saved to {output_path}")


async def main(urls: List[str]):
    """Main function with parallel site processing (for manual URL list)."""
    start_time = time.time()

    logger.info(f"Starting batch processing of {len(urls)} sites (concurrency: {CONCURRENT_SITES})")

    finder = LinkedInFinder()
    await finder.start()
    person_extractor = PersonExtractor()
    semaphore = asyncio.Semaphore(CONCURRENT_SITES)

    async with aiohttp.ClientSession() as google_session:
        try:
            tasks = [
                process_single_site(finder, google_session, url, semaphore, person_extractor)
                for url in urls
            ]
            all_results = await asyncio.gather(*tasks)
        finally:
            await finder.stop()

    import os
    output_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(output_dir, "linkedin_results.json")
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    elapsed = time.time() - start_time
    successful = sum(1 for r in all_results if not r.get("error"))
    total_linkedin = sum(len(r.get("linkedin_urls", [])) for r in all_results)
    total_emails = sum(len(r.get("emails", [])) for r in all_results)
    total_persons = sum(len(r.get("persons", [])) for r in all_results)

    logger.info("")
    logger.info(f"{'='*60}")
    logger.info(f"BATCH COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"   Sites processed: {successful}/{len(urls)}")
    logger.info(f"   LinkedIn URLs found: {total_linkedin}")
    logger.info(f"   Emails found: {total_emails}")
    logger.info(f"   Persons extracted: {total_persons}")
    logger.info(f"   Time elapsed: {elapsed:.1f}s ({elapsed/len(urls):.1f}s per site)")
    logger.info(f"{'='*60}")
    logger.info(f"Results saved to {output_path}")


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    if args and args[0] == "--sheet":
        # Fetch from Google Sheets (source sheet Alpha/Beta/Gamma)
        batch_size = int(args[1]) if len(args) > 1 else 5
        asyncio.run(main_from_sheet(batch_size, dry_run=dry_run))
    else:
        # Manual test URLs
        test_urls = [
            "https://www.assetpartners.nl/",
            "https://www.famadvocaten.nl/nl/",
            "https://www.axento.nl/"
        ]
        asyncio.run(main(test_urls))

