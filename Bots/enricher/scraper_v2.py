import asyncio
import hashlib
import re
import time
import urllib.parse
import html
import json
import logging
import ssl
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional, Set, Dict, Tuple
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

# ─── Constants ───────────────────────────────────────────────────────────────
TIMEOUT_SECONDS = 20
CONCURRENT_SITES = 3       # Sites processed simultaneously
PLAYWRIGHT_LIMIT = 6       # Max high-value pages getting full Playwright treatment
SUBPAGE_LIMIT = 40          # Max subpages to check after scoring
LEVEL2_LIMIT = 30           # Max level-2 links per high-value page
PW_CONCURRENCY = 3          # Concurrent Playwright contexts for high-value pages
XHR_MAX_BODY_SIZE = 500_000 # 500KB cap on XHR response body inspection
XHR_TIMEOUT = 2.0           # Max seconds to spend parsing XHR responses

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ─── Regex patterns ──────────────────────────────────────────────────────────
RE_LINKEDIN_URL = re.compile(
    r'(https?:\/\/(?:[a-z0-9-]+\.)?linkedin\.com\/(?:in|company|showcase|school)\/[a-zA-Z0-9%\-_.\/]+)',
    re.I
)

RE_EMAIL = re.compile(
    r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
    re.I
)

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

GENERIC_EMAIL_PREFIXES = {
    'info', 'contact', 'hello', 'hallo', 'support', 'help', 'admin', 'office',
    'team', 'sales', 'marketing', 'hr', 'careers', 'career', 'jobs', 'legal',
    'press', 'media', 'news', 'billing', 'accounts', 'noreply', 'no-reply',
    'mail', 'email', 'post', 'service', 'enquiries', 'enquiry', 'general',
    'reception', 'secretary', 'assistant', 'boekhouding', 'webmaster',
    'postmaster'
}

CTA_NOISE_TEXT = {
    'get in touch', 'read more', 'learn more', 'contact', 'contact us',
    'view profile', 'profile', 'bio'
}

MIN_CONTAINER_DESCENDANTS = 3
MAX_CONTAINER_DESCENDANTS = 50
MAX_CONTAINER_ANCESTOR_WALK = 15

# ─── Path discovery keywords & guesses ────────────────────────────────────────
INTERNAL_LINK_KEYWORDS = [
    "about", "over", "team", "contact", "people", "wie", "ons",
    "management", "board", "partner", "human", "medewerker",
    "regisseur", "adviseur", "mensen", "organisatie", "staff",
    "leadership", "founders", "employees"
]

COMMON_INTERNAL = [
    "/", "/about", "/about-us", "/over", "/over-ons", "/team", "/contact",
    "/contact-us", "/company", "/who-we-are", "/wie-zijn-wij", "/overons",
    "/ons-team", "/social", "/connect", "/footer", "/imprint", "/legal",
    "/team-van", "/onze-mensen", "/organisatie", "/medewerkers"
]

# ─── Role keywords for Strategy 1b (role-anchored card extraction) ───────────
ROLE_KEYWORDS = {
    "en": [
        "ceo", "cto", "cfo", "coo", "cmo", "cio", "cpo",
        "director", "manager", "partner", "founder", "co-founder",
        "president", "vice president", "vp", "head of", "chief",
        "consultant", "analyst", "engineer", "developer", "designer",
        "architect", "advisor", "associate", "accountant", "attorney",
        "lawyer", "specialist", "coordinator", "officer", "lead",
        "senior", "junior", "intern", "assistant", "executive",
        "supervisor", "administrator", "secretary", "treasurer",
        "chairman", "chairwoman", "board member", "sales", "consulting",
    ],
    "nl": [
        "directeur", "manager", "partner", "oprichter", "mede-oprichter",
        "bestuurder", "voorzitter", "vicevoorzitter", "hoofd",
        "adviseur", "consultant", "analist", "ingenieur", "ontwikkelaar",
        "ontwerper", "architect", "medewerker", "coördinator",
        "advocaat", "notaris", "accountant", "specialist",
        "secretaris", "penningmeester", "bestuurslid",
        "senior", "junior", "stagiair", "assistent",
        "registeraccountant", "belastingadviseur", "fiscalist",
    ],
    "de": [
        "geschäftsführer", "direktor", "manager", "partner", "gründer",
        "mitgründer", "vorstand", "vorsitzender", "leiter", "leiterin",
        "berater", "beraterin", "analyst", "ingenieur", "entwickler",
        "designer", "architekt", "mitarbeiter", "koordinator",
        "rechtsanwalt", "rechtsanwältin", "notar", "buchhalter",
        "spezialist", "sekretär", "schatzmeister", "vorstandsmitglied",
        "senior", "junior", "praktikant", "assistent",
        "steuerberater", "wirtschaftsprüfer",
    ],
}

def _detect_language(soup: BeautifulSoup) -> str:
    """Detect page language from HTML lang attribute. Returns 2-letter code or 'en' as default."""
    html_tag = soup.find('html')
    if html_tag and html_tag.get('lang'):
        lang = html_tag['lang'].lower().split('-')[0].strip()
        if lang in ROLE_KEYWORDS:
            return lang
    return "en"

# ─── Sitemap junk exclusion patterns ─────────────────────────────────────────
SITEMAP_EXCLUDE_PATTERNS = [
    "/feed", "/rss", "/wp-json", "/wp-admin", "/wp-content/uploads",
    "/cart", "/checkout", "/account", "/login", "/register", "/signin",
    "/signup", "/dashboard", "/admin", "/api/", "/cdn-cgi/",
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".zip", ".mp3", ".mp4", ".avi", ".mov",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",
    ".xml",  # nested sitemaps handled separately
]

# ─── Tracker/analytics domains to block in Playwright ─────────────────────────
BLOCKED_TRACKER_PATTERNS = [
    "*google-analytics.com*", "*googletagmanager.com*", "*doubleclick.net*",
    "*facebook.net*", "*facebook.com/tr*", "*connect.facebook.net*",
    "*hotjar.com*", "*hotjar.io*",
    "*intercom.io*", "*intercomcdn.com*",
    "*hubspot.com*", "*hs-analytics.net*", "*hs-scripts.com*",
    "*clarity.ms*", "*mouseflow.com*",
    "*segment.io*", "*segment.com*",
    "*mixpanel.com*", "*amplitude.com*",
    "*sentry.io*", "*sentry-cdn.com*",
    "*crisp.chat*", "*tawk.to*", "*livechat.com*", "*drift.com*",
    "*cookiebot.com*", "*onetrust.com*", "*cookielaw.org*",
    "*newrelic.com*", "*nr-data.net*",
    "*optimizely.com*", "*abtasty.com*",
    "*zendesk.com*", "*zdassets.com*",
    "*recaptcha.net*", "*gstatic.com/recaptcha*",
]

# ─── Level-2 junk exclusion patterns ─────────────────────────────────────────
LEVEL2_EXCLUDE_PATTERNS = [
    "/cart", "/checkout", "/account", "/login", "/register", "/signin",
    "/signup", "/dashboard", "/admin", "/api/", "/feed", "/rss",
    "/tag/", "/category/", "/author/", "?page=", "/comment",
    "/wp-admin", "/wp-json", "/cdn-cgi/",
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".zip", ".mp3", ".mp4", ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",
    "mailto:", "mail:",
]


def _make_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def dedupe_keep_order(urls: List[str]) -> List[str]:
    seen = set()
    out = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


# ─── PathCandidate: multi-signal evidence accumulator ─────────────────────────
class PathCandidate:
    """Tracks a candidate URL and the sources that discovered it."""

    WEIGHTS = {
        "keyword": 3,
        "sitemap": 2,
        "framework_global": 2,
        "xhr": 2,
        "homepage_anchor": 1,
        "common_guess": 0,
    }

    def __init__(self, url: str):
        self.url = url
        self.sources: Set[str] = set()

    def add_source(self, source: str):
        self.sources.add(source)

    @property
    def score(self) -> int:
        total = sum(self.WEIGHTS.get(s, 0) for s in self.sources)
        # Keyword bonus only applies when URL has a real discovery source (not just common_guess)
        real_sources = self.sources - {"common_guess"}
        if real_sources and any(k in self.url.lower() for k in INTERNAL_LINK_KEYWORDS):
            total += self.WEIGHTS["keyword"]
        return total


class CandidatePool:
    """Collects URLs from all discovery sources, deduplicates, scores, and ranks."""

    def __init__(self, base_url: str):
        parsed = urllib.parse.urlparse(base_url)
        self.base = f"{parsed.scheme}://{parsed.netloc}"
        self.base_domain = parsed.netloc.replace("www.", "")
        self.start_url = base_url
        self._candidates: Dict[str, PathCandidate] = {}

    def _normalize(self, url: str) -> Optional[str]:
        url = url.strip().rstrip("/")
        if not url:
            return None
        # Skip non-http URLs (mailto:, tel:, javascript:, etc.)
        if url.startswith(("mailto:", "mail:", "tel:", "javascript:", "data:", "#")):
            return None
        # Skip the start URL itself
        if url.rstrip("/") == self.start_url.rstrip("/"):
            return None
        # Must be same origin
        try:
            parsed = urllib.parse.urlparse(url)
            domain = parsed.netloc.replace("www.", "")
            if domain and domain != self.base_domain:
                return None
        except Exception:
            return None
        return url

    def add(self, url: str, source: str):
        normalized = self._normalize(url)
        if not normalized:
            return
        if normalized not in self._candidates:
            self._candidates[normalized] = PathCandidate(normalized)
        self._candidates[normalized].add_source(source)

    def add_many(self, urls: List[str], source: str):
        for u in urls:
            self.add(u, source)

    def add_paths(self, paths: List[str], source: str):
        for path in paths:
            full = urllib.parse.urljoin(self.base, path)
            self.add(full, source)

    def ranked(self, limit: int = SUBPAGE_LIMIT) -> List[str]:
        ranked = sorted(
            self._candidates.values(),
            key=lambda c: (c.score, c.url),
            reverse=True
        )
        return [c.url for c in ranked[:limit]]

    def get_score(self, url: str) -> int:
        normalized = url.rstrip("/")
        c = self._candidates.get(normalized)
        return c.score if c else 0

    def get_sources(self, url: str) -> Set[str]:
        normalized = url.rstrip("/")
        c = self._candidates.get(normalized)
        return c.sources if c else set()


@dataclass
class PageArtifact:
    url: str
    soup: Optional[BeautifulSoup]
    emails: Set[str]
    linkedin_urls: Set[str]


class LinkedInFinder:
    def __init__(self):
        self.browser = None
        self.playwright = None
        self._ssl_ctx = _make_ssl_context()

    async def start(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)

    async def stop(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    # ─── Extraction helpers (unchanged from v1) ───────────────────────────────

    def _clean_url(self, url: str) -> Optional[str]:
        if not url:
            return None
        url = html.unescape(url).strip()
        url = url.rstrip('/')
        url = url.rstrip('\\"')
        url = url.rstrip('"')
        url = url.rstrip("'")
        try:
            parsed = urllib.parse.urlparse(url)
            if 'linkedin.com' not in parsed.netloc:
                return None
            path = parsed.path.lower()
            if any(x in path for x in ['/share', '/sharearticle', '/embed', '/pulse/']):
                return None
            if not any(x in path for x in ['/company/', '/in/', '/showcase/']):
                return None
            url = urllib.parse.urlunparse(parsed._replace(query="", fragment=""))
            return url
        except Exception:
            return None

    def _extract_from_text(self, text: str) -> Set[str]:
        found = set()
        for m in RE_LINKEDIN_URL.findall(text):
            cleaned = self._clean_url(m)
            if cleaned:
                found.add(cleaned)
        return found

    def _extract_emails_from_text(self, text: str, site_domain: str = "") -> Set[str]:
        found = set()
        for email in RE_EMAIL.findall(text):
            email = email.lower().strip()
            local_part, _, domain = email.partition('@')
            if any(excl in email for excl in EXCLUDED_EMAILS):
                continue
            if domain in EXCLUDED_EMAIL_DOMAINS or domain.endswith('.wixpress.com'):
                continue
            if RE_MACHINE_EMAIL_LOCAL.fullmatch(local_part):
                continue
            if any(ext in email for ext in ['.png', '.jpg', '.gif', '.svg', '.css', '.js']):
                continue
            found.add(email)
        return found

    def _extract_from_soup(self, soup: BeautifulSoup, base_url: str) -> Set[str]:
        found = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            full_url = urllib.parse.urljoin(base_url, href)
            if 'linkedin.com' in full_url:
                cleaned = self._clean_url(full_url)
                if cleaned:
                    found.add(cleaned)
        for tag in soup.find_all(True):
            for attr in ['data-href', 'data-url', 'content']:
                if tag.has_attr(attr):
                    val = tag[attr]
                    if 'linkedin.com' in val:
                        cleaned = self._clean_url(val)
                        if cleaned:
                            found.add(cleaned)
        return found

    @staticmethod
    def _make_page_artifact(url: str, soup: Optional[BeautifulSoup],
                            linkedin_urls: Set[str], emails: Set[str]) -> Optional[PageArtifact]:
        if not soup:
            return None
        normalized_url = url.rstrip('/') or url
        return PageArtifact(
            url=normalized_url,
            soup=soup,
            linkedin_urls=set(linkedin_urls or set()),
            emails=set(emails or set())
        )

    @staticmethod
    def _merge_page_artifact(artifacts: Dict[str, PageArtifact], artifact: Optional[PageArtifact]):
        if not artifact:
            return
        existing = artifacts.get(artifact.url)
        if not existing:
            artifacts[artifact.url] = artifact
            return
        existing.linkedin_urls.update(artifact.linkedin_urls)
        existing.emails.update(artifact.emails)
        if existing.soup is None and artifact.soup is not None:
            existing.soup = artifact.soup

    # ─── Phase 1: Sitemap fetch ───────────────────────────────────────────────

    async def _fetch_sitemap(self, session: aiohttp.ClientSession, base_url: str) -> List[str]:
        """Fetch /sitemap.xml, parse all same-origin HTML-like URLs.
        Exclude only obvious junk. No keyword filtering — scoring handles relevance.
        Follows nested sitemapindex entries one level deep."""
        parsed_base = urllib.parse.urlparse(base_url)
        sitemap_url = f"{parsed_base.scheme}://{parsed_base.netloc}/sitemap.xml"
        all_urls = []

        try:
            urls_from = await self._parse_sitemap_url(session, sitemap_url)
            all_urls.extend(urls_from)
        except Exception as e:
            logger.debug(f"Sitemap fetch failed for {sitemap_url}: {e}")

        if all_urls:
            logger.info(f"Sitemap: discovered {len(all_urls)} candidate URLs")
        else:
            logger.debug(f"No sitemap found or empty at {sitemap_url}")

        return all_urls

    async def _parse_sitemap_url(self, session: aiohttp.ClientSession, sitemap_url: str) -> List[str]:
        """Parse a single sitemap URL. Handles both sitemap and sitemapindex."""
        urls = []
        try:
            async with session.get(
                sitemap_url,
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=10),
                ssl=self._ssl_ctx
            ) as resp:
                if resp.status != 200:
                    return urls
                text = await resp.text()
        except Exception:
            return urls

        try:
            # Strip namespace for easier parsing
            text_clean = re.sub(r'\sxmlns="[^"]+"', '', text, count=1)
            root = ET.fromstring(text_clean)
        except ET.ParseError:
            return urls

        # Check if this is a sitemapindex (contains nested sitemaps)
        sitemap_entries = root.findall('.//sitemap/loc')
        if sitemap_entries:
            # Parse nested sitemaps (one level deep only)
            nested_tasks = []
            for entry in sitemap_entries[:10]:  # Cap at 10 nested sitemaps
                nested_url = (entry.text or "").strip()
                if nested_url:
                    nested_tasks.append(self._parse_sitemap_url(session, nested_url))
            if nested_tasks:
                nested_results = await asyncio.gather(*nested_tasks, return_exceptions=True)
                for result in nested_results:
                    if isinstance(result, list):
                        urls.extend(result)
            return urls

        # Regular sitemap — extract <url><loc> entries
        for loc in root.findall('.//url/loc'):
            url = (loc.text or "").strip()
            if not url:
                continue
            # Exclude obvious junk
            url_lower = url.lower()
            if any(pat in url_lower for pat in SITEMAP_EXCLUDE_PATTERNS):
                continue
            urls.append(url)

        return urls

    # ─── Phase 1: Framework globals extraction ────────────────────────────────

    async def _extract_framework_routes(self, page) -> List[str]:
        """Extract route paths from framework globals (__NEXT_DATA__, __NUXT__, __remixContext).
        Called inside _process_playwright before page close."""
        routes = await page.evaluate("""() => {
            const paths = new Set();

            // Next.js
            try {
                const nd = window.__NEXT_DATA__;
                if (nd) {
                    // buildManifest pages
                    if (nd.buildManifest && nd.buildManifest.sortedPages) {
                        nd.buildManifest.sortedPages.forEach(p => paths.add(p));
                    }
                    // page itself
                    if (nd.page) paths.add(nd.page);
                    // props may contain links
                    const propsStr = JSON.stringify(nd.props || {});
                    const linkRe = /(?:href|url|link|path)["']?\\s*[:=]\\s*["']\\/([^"'\\s]+)/gi;
                    let m;
                    while ((m = linkRe.exec(propsStr)) !== null) {
                        paths.add('/' + m[1]);
                    }
                }
            } catch(e) {}

            // Nuxt.js
            try {
                const nuxt = window.__NUXT__ || window.__nuxt;
                if (nuxt) {
                    const nuxtStr = JSON.stringify(nuxt);
                    const linkRe = /(?:href|url|link|path|to)["']?\\s*[:=]\\s*["']\\/([^"'\\s]+)/gi;
                    let m;
                    while ((m = linkRe.exec(nuxtStr)) !== null) {
                        paths.add('/' + m[1]);
                    }
                }
            } catch(e) {}

            // Remix
            try {
                const remix = window.__remixContext;
                if (remix) {
                    const remixStr = JSON.stringify(remix);
                    const linkRe = /(?:href|url|link|path)["']?\\s*[:=]\\s*["']\\/([^"'\\s]+)/gi;
                    let m;
                    while ((m = linkRe.exec(remixStr)) !== null) {
                        paths.add('/' + m[1]);
                    }
                }
            } catch(e) {}

            // Gatsby
            try {
                if (window.___gatsby || window.__PATH_PREFIX__ !== undefined) {
                    const pageData = document.getElementById('___gatsby');
                    if (pageData) {
                        const links = pageData.querySelectorAll('a[href^="/"]');
                        links.forEach(a => paths.add(a.getAttribute('href')));
                    }
                }
            } catch(e) {}

            return Array.from(paths).filter(p => p && p.startsWith('/'));
        }""")
        if routes:
            logger.info(f"Framework globals: discovered {len(routes)} routes")
        return routes or []

    # ─── Phase 1: XHR/response interception ───────────────────────────────────

    def _setup_xhr_interception(self, page, base_domain: str) -> Dict:
        """Register request/response listeners. Returns a state dict that accumulates paths."""
        state = {
            "paths": [],
            "start_time": time.time(),
        }

        async def on_response(response):
            # Time cap
            if time.time() - state["start_time"] > XHR_TIMEOUT:
                return
            try:
                url = response.url
                parsed = urllib.parse.urlparse(url)
                resp_domain = parsed.netloc.replace("www.", "")

                # Only same-origin requests
                if resp_domain != base_domain:
                    return

                # Skip non-data content types
                content_type = response.headers.get("content-type", "")
                if not any(t in content_type for t in ["json", "html", "text/plain"]):
                    return

                # Record the URL path itself as a candidate
                if parsed.path and parsed.path != "/":
                    state["paths"].append(parsed.path)

                # Inspect JSON response body for embedded paths (size-capped)
                if "json" in content_type:
                    try:
                        body = await response.body()
                        if len(body) > XHR_MAX_BODY_SIZE:
                            return
                        text = body.decode("utf-8", errors="ignore")
                        # Extract internal paths from JSON
                        path_re = re.compile(r'["\'](/[a-zA-Z0-9\-_/]+(?:\.[a-zA-Z]{2,4})?)["\']')
                        for m in path_re.findall(text):
                            # Skip assets
                            if any(m.lower().endswith(ext) for ext in ['.js', '.css', '.png', '.jpg', '.svg', '.woff', '.woff2', '.gif']):
                                continue
                            state["paths"].append(m)
                    except Exception:
                        pass
            except Exception:
                pass

        page.on("response", on_response)
        return state

    # ─── Static fetch (extraction only) ───────────────────────────────────────

    async def _fetch_static(self, session: aiohttp.ClientSession, url: str) -> Tuple[Set[str], Set[str]]:
        """Fetch a page statically. Returns (linkedin_urls, emails)."""
        try:
            async with session.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=10),
                ssl=self._ssl_ctx
            ) as response:
                if response.status != 200:
                    return set(), set()
                text = await response.text()
                soup = BeautifulSoup(text, 'html.parser')
                found = self._extract_from_soup(soup, str(response.url))
                found.update(self._extract_from_text(text))
                emails = self._extract_emails_from_text(text)
                return found, emails
        except Exception as e:
            logger.warning(f"Static fetch failed for {url}: {e}")
            return set(), set()

    # ─── Phase 3.9: Adaptive wait ───────────────────────────────────────────────

    async def _wait_until_ready(self, page, ceiling: float = 3.0, poll_interval: float = 0.2):
        """Wait for page content to stabilize using multiple signals.
        Polls anchor count, text length, and element count every poll_interval seconds.
        Declares ready when all three stabilize for 500ms, or ceiling is reached.
        Falls back to 'load' event if result looks sparse after ceiling."""
        stable_since = None
        last_snapshot = None
        start = time.time()

        while time.time() - start < ceiling:
            snapshot = await page.evaluate("""() => ({
                anchors: document.querySelectorAll('a[href]').length,
                textLen: (document.body && document.body.innerText || '').length,
                elements: document.querySelectorAll('*').length,
                hasTeamContent: !!(
                    document.querySelector('.team, [class*="people"], [class*="medewerker"], [class*="team"], [class*="staff"], [class*="employee"]') ||
                    document.querySelector('[id*="team"], [id*="people"], [id*="medewerker"]')
                )
            })""")

            if snapshot.get("hasTeamContent"):
                # Found team-like content — ready
                return

            if last_snapshot and (
                snapshot["anchors"] == last_snapshot["anchors"]
                and snapshot["textLen"] == last_snapshot["textLen"]
                and snapshot["elements"] == last_snapshot["elements"]
            ):
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= 0.5:
                    # Stable for 500ms — ready
                    return
            else:
                stable_since = None

            last_snapshot = snapshot
            await asyncio.sleep(poll_interval)

        # Ceiling reached — check if result looks sparse
        if last_snapshot and last_snapshot["anchors"] < 3 and last_snapshot["textLen"] < 200:
            # Sparse result — try waiting for 'load' as fallback
            try:
                await page.wait_for_load_state("load", timeout=3000)
            except Exception:
                pass

    # ─── Playwright processing (with Phase 1 additions) ──────────────────────

    async def _process_playwright(self, url: str) -> Dict:
        """Render page with Playwright. Extract LinkedIn URLs, internal links, emails,
        framework routes, and XHR-discovered paths.

        Returns dict with keys: linkedin, internal_links, emails, framework_routes, xhr_paths
        """
        found = set()
        page = None
        context = None
        xhr_paths = []
        framework_routes = []
        try:
            context = await self.browser.new_context(user_agent=USER_AGENT)
            page = await context.new_page()

            # Block static assets
            await page.route("**/*.{png,jpg,jpeg,gif,css,woff,woff2}", lambda route: route.abort())

            # Block trackers/analytics
            for pattern in BLOCKED_TRACKER_PATTERNS:
                await page.route(pattern, lambda route: route.abort())

            # Set up XHR/response interception
            base_domain = urllib.parse.urlparse(url).netloc.replace("www.", "")
            xhr_state = self._setup_xhr_interception(page, base_domain)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_SECONDS * 1000)
                await self._wait_until_ready(page)
            except PlaywrightTimeoutError:
                logger.warning(f"Timeout loading {url} in Playwright, proceeding with what loaded.")

            # Extract framework globals before doing anything else
            framework_routes = await self._extract_framework_routes(page)

            # Collect XHR-discovered paths
            xhr_paths = list(xhr_state["paths"])
            if xhr_paths:
                logger.info(f"XHR interception: discovered {len(xhr_paths)} paths from {url}")

            # 1) Extract anchors with text
            anchors = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                    href: a.href,
                    text: (a.innerText || a.textContent || '').trim().toLowerCase(),
                    aria: (a.getAttribute('aria-label') || '').trim().toLowerCase(),
                    title: (a.getAttribute('title') || '').trim().toLowerCase()
                }));
            }""")

            internal_links_ordered = []
            cta_words = {"read more", "lees meer", "view profile", "profile", "bio"}

            for a in anchors:
                href = a.get("href") or ""
                if not href or href.startswith(("mailto:", "mail:", "tel:", "javascript:", "#")):
                    continue

                if "linkedin.com" in href:
                    cleaned = self._clean_url(href)
                    if cleaned:
                        found.add(cleaned)
                    continue

                try:
                    parsed = urllib.parse.urlparse(href)
                    href_domain = parsed.netloc.replace("www.", "")
                    is_internal = (not parsed.netloc) or (href_domain == base_domain)
                    if not is_internal:
                        continue

                    full_int_url = urllib.parse.urljoin(url, href)

                    if any(full_int_url.lower().endswith(ext) for ext in (
                        ".js", ".css", ".png", ".jpg", ".jpeg", ".svg",
                        ".woff", ".woff2", ".gif", ".pdf", ".ttf", ".eot"
                    )):
                        continue

                    internal_links_ordered.append(full_int_url)

                    label = " ".join([a.get("text", ""), a.get("aria", ""), a.get("title", "")]).strip()
                    if label in cta_words:
                        internal_links_ordered.append(full_int_url)

                except Exception:
                    pass

            # 2) Extract from rendered HTML
            content = await page.content()
            found.update(self._extract_from_text(content))

            # 2b) Extract emails
            emails = self._extract_emails_from_text(content)

            # 3) Internal links from raw HTML regex
            re_internal = re.compile(
                r'(https?:\/\/(?:www\.)?' + re.escape(base_domain) + r'\/[a-zA-Z0-9%\-_.\/]+)',
                re.I
            )
            for m in re_internal.findall(content):
                m = m.strip('"\'')
                if any(ext in m.lower() for ext in ['.js', '.css', '.png', '.jpg', '.jpeg', '.svg', '.woff', '.woff2', '.gif', '.pdf', '.ttf', '.eot']):
                    continue
                internal_links_ordered.append(m)

            internal_links = dedupe_keep_order(internal_links_ordered)

            soup = BeautifulSoup(content, 'html.parser')

            return {
                "linkedin": found,
                "internal_links": internal_links,
                "emails": emails,
                "framework_routes": framework_routes,
                "xhr_paths": xhr_paths,
                "soup": soup,
            }

        except Exception as e:
            logger.error(f"Playwright error for {url}: {e}")
            return {
                "linkedin": found,
                "internal_links": [],
                "emails": set(),
                "framework_routes": [],
                "xhr_paths": [],
                "soup": None,
            }
        finally:
            if page:
                await page.close()
            if context:
                await context.close()

    # ─── Main orchestration ───────────────────────────────────────────────────

    async def find_linkedin_links(self, start_url: str) -> Tuple[List[str], List[str], List[PageArtifact]]:
        """Main method: find LinkedIn links and emails for a website.
        Returns: (linkedin_urls, emails, page_artifacts)
        """
        if not start_url.startswith('http'):
            start_url = 'https://' + start_url

        all_found: Set[str] = set()
        all_emails: Set[str] = set()
        page_artifacts: Dict[str, PageArtifact] = {}

        parsed = urllib.parse.urlparse(start_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # Build candidate pool
        pool = CandidatePool(start_url)

        # ── Phase 1: Homepage pass (Playwright + static + sitemap in parallel) ──

        async def _homepage_playwright():
            return await self._process_playwright(start_url)

        async def _homepage_static():
            try:
                async with aiohttp.ClientSession() as tmp_session:
                    async with tmp_session.get(
                        start_url,
                        headers={"User-Agent": USER_AGENT},
                        timeout=aiohttp.ClientTimeout(total=15),
                        ssl=self._ssl_ctx
                    ) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            soup = BeautifulSoup(text, 'html.parser')
                            li = self._extract_from_soup(soup, start_url)
                            li.update(self._extract_from_text(text))
                            emails = self._extract_emails_from_text(text)
                            artifact = self._make_page_artifact(str(resp.url), soup, li, emails)
                            return li, emails, soup, artifact
            except Exception:
                pass
            return set(), set(), None, None

        async def _homepage_sitemap():
            try:
                async with aiohttp.ClientSession() as tmp_session:
                    return await self._fetch_sitemap(tmp_session, start_url)
            except Exception:
                return []

        logger.info(f"Processing {start_url} — homepage pass (Playwright + static + sitemap)...")

        pw_result, static_result, sitemap_urls = await asyncio.gather(
            _homepage_playwright(),
            _homepage_static(),
            _homepage_sitemap()
        )

        # Unpack Playwright results
        all_found.update(pw_result["linkedin"])
        all_emails.update(pw_result["emails"])
        discovered_internal_links = pw_result["internal_links"]
        framework_routes = pw_result["framework_routes"]
        xhr_paths = pw_result["xhr_paths"]
        self._merge_page_artifact(
            page_artifacts,
            self._make_page_artifact(start_url, pw_result.get("soup"), pw_result["linkedin"], pw_result["emails"])
        )

        # Unpack static results
        static_linkedin, static_emails, homepage_soup, homepage_static_artifact = static_result
        all_found.update(static_linkedin)
        all_emails.update(static_emails)
        self._merge_page_artifact(page_artifacts, homepage_static_artifact)

        if all_found:
            logger.info(f"Found {len(all_found)} LinkedIn URLs on homepage")

        # ── Feed all discovery sources into the candidate pool ──

        # COMMON_INTERNAL guesses (score +0 unless corroborated)
        pool.add_paths(COMMON_INTERNAL, "common_guess")

        # Homepage anchor-discovered links (+1)
        pool.add_many(discovered_internal_links, "homepage_anchor")

        # Sitemap-discovered URLs (+2)
        pool.add_many(sitemap_urls, "sitemap")

        # Framework globals discovered routes (+2)
        for path in framework_routes:
            full = urllib.parse.urljoin(base, path)
            pool.add(full, "framework_global")

        # XHR/response-discovered paths (+2)
        for path in xhr_paths:
            full = urllib.parse.urljoin(base, path)
            pool.add(full, "xhr")

        logger.info(f"Candidate pool: {len(pool._candidates)} unique URLs after all discovery sources")

        # ── Score, rank, and truncate ──
        final_list = pool.ranked(SUBPAGE_LIMIT)

        logger.info(f"Top {len(final_list)} subpages selected for crawling")
        for u in final_list[:5]:
            logger.info(f"  [{pool.get_score(u)}] {u} ({', '.join(pool.get_sources(u))})")

        # ── Phase 2: Subpage crawl ──
        # High-value pages get full Playwright + static treatment (concurrent, bounded)
        # Remaining pages get static-only (concurrent, bounded)

        pw_semaphore = asyncio.Semaphore(PW_CONCURRENCY)
        static_semaphore = asyncio.Semaphore(10)
        already_checked: Set[str] = set()
        # Lock for thread-safe updates to shared state from concurrent high-value tasks
        results_lock = asyncio.Lock()

        async def _bounded_static(session, url):
            async with static_semaphore:
                return await self._fetch_static(session, url)

        async def _process_high_value(session, page_url):
            """Full treatment for a high-value page: Playwright + static + level-2."""
            async with pw_semaphore:
                logger.info(f"  -> Full check (Playwright + static): {page_url}")

                pw_res, static_res = await asyncio.gather(
                    self._process_playwright(page_url),
                    self._fetch_static(session, page_url)
                )

                new_links = pw_res["internal_links"]
                st_li, st_em = static_res

                artifact_linkedin = pw_res["linkedin"] | st_li
                artifact_emails = pw_res["emails"] | st_em
                page_linkedin = set(artifact_linkedin)
                page_emails = set(artifact_emails)

                # Level-2 crawl: check new internal links from this page
                # Strip fragments and query params for canonical dedup
                cleaned_links = []
                for l in new_links:
                    # Remove fragment
                    l = l.split('#')[0]
                    # Remove query params for dedup purposes
                    l = l.split('?')[0]
                    l = l.rstrip('/')
                    if l:
                        cleaned_links.append(l)
                cleaned_links = dedupe_keep_order(cleaned_links)

                async with results_lock:
                    level2_links = [
                        l for l in cleaned_links
                        if l not in already_checked
                        and l.rstrip('/') != start_url.rstrip('/')
                        and not any(pat in l.lower() for pat in LEVEL2_EXCLUDE_PATTERNS)
                    ]
                    level2_links = level2_links[:LEVEL2_LIMIT]
                    already_checked.update(level2_links)

                if level2_links:
                    logger.info(f"    -> {len(level2_links)} level-2 links from {page_url}")
                    l2_tasks = [_bounded_static(session, l2) for l2 in level2_links]
                    l2_results = await asyncio.gather(*l2_tasks)
                    for l2_li, l2_em in l2_results:
                        page_linkedin.update(l2_li)
                        page_emails.update(l2_em)

                artifact = self._make_page_artifact(page_url, pw_res.get("soup"), artifact_linkedin, artifact_emails)
                return page_linkedin, page_emails, artifact

        async with aiohttp.ClientSession() as session:
            # Split final_list into high-value (Playwright) and static-only
            # final_list is already sorted by score descending from pool.ranked()
            # Use the score to decide: top-scored pages with score > 0 get Playwright
            high_value_urls = []
            static_urls = []

            for url in final_list:
                score = pool.get_score(url)
                if score > 0 and len(high_value_urls) < PLAYWRIGHT_LIMIT:
                    high_value_urls.append(url)
                    already_checked.add(url)
                else:
                    static_urls.append(url)
                    already_checked.add(url)

            logger.info(f"  {len(high_value_urls)} high-value (Playwright), {len(static_urls)} static-only")

            # Run high-value pages concurrently (bounded by PW_CONCURRENCY)
            # and static-only pages concurrently (bounded by static_semaphore)
            # Both groups run in parallel with each other
            hv_tasks = [_process_high_value(session, u) for u in high_value_urls]
            st_tasks = [_bounded_static(session, u) for u in static_urls]

            hv_results, st_results = await asyncio.gather(
                asyncio.gather(*hv_tasks),
                asyncio.gather(*st_tasks)
            )

            for hv_li, hv_em, hv_artifact in hv_results:
                all_found.update(hv_li)
                all_emails.update(hv_em)
                self._merge_page_artifact(page_artifacts, hv_artifact)

            for st_li, st_em in st_results:
                all_found.update(st_li)
                all_emails.update(st_em)

        return sorted(list(all_found)), sorted(list(all_emails)), list(page_artifacts.values())


# ─── Orchestration functions (kept from v1) ───────────────────────────────────

def generate_company_id(company_name: str) -> str:
    date_prefix = datetime.now().strftime("%Y%m%d")
    hash_suffix = hashlib.md5(company_name.lower().strip().encode()).hexdigest()[:6]
    return f"{date_prefix}-{hash_suffix}"


class PersonExtractor:
    """Extracts person data (name, role, email, linkedin) using 5 fallback strategies."""

    def __init__(self):
        self._ssl_context = None

    def _get_ssl_context(self):
        if not self._ssl_context:
            self._ssl_context = _make_ssl_context()
        return self._ssl_context

    def _role_keywords_for_soup(self, soup: BeautifulSoup) -> List[str]:
        lang = _detect_language(soup)
        keywords = ROLE_KEYWORDS.get(lang, []) + (ROLE_KEYWORDS["en"] if lang != "en" else [])
        return list(dict.fromkeys(kw.lower() for kw in keywords))

    def _matches_role(self, text: str, role_keywords: List[str]) -> bool:
        t = (text or "").strip().lower()
        return any(t == kw or t.startswith(kw) or t.endswith(kw) or kw in t for kw in role_keywords)

    @staticmethod
    def _looks_like_person_name(text: str) -> bool:
        clean = re.sub(r'\s+', ' ', (text or '').strip())
        words = clean.split()
        if len(words) < 2 or len(words) > 4:
            return False
        pattern = re.compile(r"^[A-Z\u00C0-\u024F][A-Za-z\u00C0-\u024F'’\-]+$")
        return all(pattern.match(word) for word in words)

    @staticmethod
    def _node_text(node) -> str:
        return node.get_text(" ", strip=True) if node else ""

    def _is_noise_text(self, text: str) -> bool:
        t = (text or "").strip().lower()
        if not t:
            return True
        if t in CTA_NOISE_TEXT:
            return True
        if '@' in t:
            return True
        if t.startswith('http'):
            return True
        return False

    def _get_leaf_nodes(self, container) -> List:
        leaves = []
        for tag in container.find_all(True):
            if tag.name in {'script', 'style', 'svg', 'path'}:
                continue
            if any(getattr(child, 'name', None) for child in tag.children):
                continue
            text = self._node_text(tag)
            if len(text) <= 1 or len(text) >= 400 or self._is_noise_text(text):
                continue
            leaves.append(tag)
        return leaves

    @staticmethod
    def _parse_name_from_email(email: str) -> Dict:
        if not email:
            return {"tokens": [], "raw": "", "is_generic": True}
        local = email.split('@')[0].lower()
        return {
            "tokens": [t for t in re.split(r'[.\-_+]', local) if len(t) > 1],
            "raw": local,
            "is_generic": local in GENERIC_EMAIL_PREFIXES or len(local) <= 2,
        }

    @staticmethod
    def _parse_name_from_linkedin(url: str) -> Dict:
        if not url:
            return {"tokens": [], "raw": ""}
        match = re.search(r'linkedin\.com/in/([^/?#]+)', url, re.I)
        if not match:
            return {"tokens": [], "raw": ""}
        slug = match.group(1).lower()
        slug = re.sub(r'-\d+$', '', slug)
        return {
            "tokens": [t for t in slug.split('-') if len(t) > 1],
            "raw": slug
        }

    @staticmethod
    def _clean_linkedin_url(url: str) -> str:
        if not url:
            return ""
        return url.rstrip('/').split('?')[0]

    def _email_identity_key(self, email: str) -> str:
        if not email:
            return ""
        email = email.strip().lower()
        local = email.split('@')[0]
        if local in GENERIC_EMAIL_PREFIXES or len(local) <= 2:
            return ""
        return email

    def _find_email(self, container) -> str:
        mailto = container.find('a', href=re.compile(r'^mailto:', re.I))
        if not mailto:
            return ""
        return mailto.get('href', '').replace('mailto:', '').strip()

    def _find_personal_linkedin(self, container) -> str:
        for anchor in container.find_all('a', href=True):
            href = anchor.get('href', '')
            if 'linkedin.com/in/' in href.lower():
                return self._clean_linkedin_url(href)
        return ""

    def _find_any_linkedin(self, container) -> str:
        for anchor in container.find_all('a', href=True):
            href = anchor.get('href', '')
            if 'linkedin.com' in href.lower():
                return self._clean_linkedin_url(href)
        return ""

    def _container_descendants(self, container) -> int:
        return len(container.find_all(True))

    def _is_valid_container(self, container) -> bool:
        count = self._container_descendants(container)
        if count < MIN_CONTAINER_DESCENDANTS or count > MAX_CONTAINER_DESCENDANTS:
            return False
        return bool(
            container.find('a', href=re.compile(r'^mailto:', re.I)) or
            container.find('a', href=re.compile(r'linkedin\.com', re.I))
        )

    def _corroborate_name(self, container, email: str, linkedin: str,
                          role_keywords: List[str]) -> Optional[Dict]:
        e_info = self._parse_name_from_email(email)
        l_info = self._parse_name_from_linkedin(linkedin)

        exact_tokens = set(([] if e_info["is_generic"] else e_info["tokens"]) + l_info["tokens"])
        email_sub = not e_info["is_generic"] and len(e_info["tokens"]) <= 1 and len(e_info["raw"]) > 2
        linkedin_sub = len(l_info["tokens"]) <= 1 and len(l_info["raw"]) > 2

        leaves = [leaf for leaf in self._get_leaf_nodes(container) if len(self._node_text(leaf)) < 120]

        best_leaf = None
        best_score = 0
        for leaf in leaves:
            text = self._node_text(leaf)
            if not self._looks_like_person_name(text) or self._matches_role(text, role_keywords):
                continue
            tokens = [t for t in re.split(r"[\s\-'.]+", text.lower()) if len(t) > 1]
            score = sum(2 for token in tokens if token in exact_tokens)
            if email_sub:
                for token in tokens:
                    if len(token) > 2 and (e_info["raw"].find(token) != -1 or token.find(e_info["raw"]) != -1):
                        score += 1
            if linkedin_sub:
                for token in tokens:
                    if len(token) > 2 and (l_info["raw"].find(token) != -1 or token.find(l_info["raw"]) != -1):
                        score += 1
            if score > best_score:
                best_score = score
                best_leaf = leaf

        if best_leaf and best_score >= 1:
            return {
                "name_node": best_leaf,
                "confidence": "primary",
                "strategy": "container_primary",
                "evidence": "personal_signal_match",
            }

        role_leaf = next((leaf for leaf in leaves if self._matches_role(self._node_text(leaf), role_keywords)), None)
        person_leaves = [
            leaf for leaf in leaves
            if self._looks_like_person_name(self._node_text(leaf))
            and not self._matches_role(self._node_text(leaf), role_keywords)
        ]
        if (e_info["is_generic"] or (not e_info["raw"] and not l_info["raw"])) and role_leaf and len(person_leaves) == 1:
            return {
                "name_node": person_leaves[0],
                "confidence": "secondary",
                "strategy": "container_secondary",
                "evidence": "generic_contact_structural_match",
            }
        return None

    def _extract_person_from_container(self, container, name_node, page_url: str,
                                       confidence: str, strategy: str,
                                       evidence: str, role_keywords: List[str]) -> Optional[Dict]:
        name = self._node_text(name_node)
        if not name:
            return None

        leaves = self._get_leaf_nodes(container)
        role_leaf = None
        for leaf in leaves:
            text = self._node_text(leaf)
            if text == name or len(text) >= 120:
                continue
            if self._matches_role(text, role_keywords):
                role_leaf = leaf
                break

        bio_leaf = None
        bio_candidates = []
        for leaf in leaves:
            text = self._node_text(leaf)
            if text == name or leaf == role_leaf:
                continue
            if len(text) > 40 and not self._matches_role(text, role_keywords):
                bio_candidates.append(leaf)
        if bio_candidates:
            bio_leaf = max(bio_candidates, key=lambda leaf: len(self._node_text(leaf)))

        if not role_leaf:
            for leaf in leaves:
                text = self._node_text(leaf)
                if text == name or leaf == bio_leaf or len(text) >= 120:
                    continue
                if text.lower() not in CTA_NOISE_TEXT:
                    role_leaf = leaf
                    break

        return {
            "name": name,
            "role": self._node_text(role_leaf) or "",
            "linkedin": self._find_personal_linkedin(container),
            "email": self._find_email(container),
            "bio": self._node_text(bio_leaf) or "",
            "confidence": confidence,
            "strategy": strategy,
            "evidence": evidence,
            "source_url": page_url,
        }

    def _discover_container_candidates(self, soup: BeautifulSoup, page_url: str) -> List[Dict]:
        role_keywords = self._role_keywords_for_soup(soup)
        candidates: Dict[int, Dict] = {}
        leaves = [leaf for leaf in self._get_leaf_nodes(soup) if len(self._node_text(leaf)) < 120]

        def try_add(node, layer: int) -> bool:
            if not node or not getattr(node, 'name', None):
                return False
            if not self._is_valid_container(node):
                return False
            email = self._find_email(node)
            linkedin = self._find_personal_linkedin(node) or self._find_any_linkedin(node)
            corroborated = self._corroborate_name(node, email, linkedin, role_keywords)
            if not corroborated:
                return False
            existing = candidates.get(id(node))
            if existing:
                current_rank = 2 if corroborated["confidence"] == "primary" else 1
                existing_rank = 2 if existing["confidence"] == "primary" else 1
                if current_rank <= existing_rank:
                    return True
            candidates[id(node)] = {
                "container": node,
                "name_node": corroborated["name_node"],
                "confidence": corroborated["confidence"],
                "strategy": corroborated["strategy"],
                "evidence": corroborated["evidence"],
                "page_url": page_url,
                "role_keywords": role_keywords,
                "layer": layer,
            }
            return True

        for leaf in leaves:
            if not self._matches_role(self._node_text(leaf), role_keywords):
                continue
            current = leaf
            for _ in range(MAX_CONTAINER_ANCESTOR_WALK):
                if try_add(current, 1):
                    break
                current = current.parent
                if current is None:
                    break

        contact_anchors = []
        for anchor in soup.find_all('a', href=True):
            href = anchor.get('href', '')
            if href.lower().startswith('mailto:') or 'linkedin.com' in href.lower():
                contact_anchors.append(anchor)

        for anchor in contact_anchors:
            current = anchor
            for _ in range(MAX_CONTAINER_ANCESTOR_WALK):
                if try_add(current, 2):
                    break
                current = current.parent
                if current is None:
                    break

        persons = []
        for candidate in candidates.values():
            person = self._extract_person_from_container(
                candidate["container"],
                candidate["name_node"],
                candidate["page_url"],
                candidate["confidence"],
                candidate["strategy"],
                candidate["evidence"],
                candidate["role_keywords"],
            )
            if person:
                persons.append(person)
        return self._dedupe_persons(persons)

    def extract_from_page_artifact(self, artifact: PageArtifact) -> List[Dict]:
        if not artifact or not artifact.soup:
            return []
        return self._discover_container_candidates(artifact.soup, artifact.url)

    def strategy2_json_ld(self, soup: BeautifulSoup) -> List[Dict]:
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
            same_as = item.get('sameAs', [])
            if isinstance(same_as, str):
                same_as = [same_as]
            for url in same_as:
                if 'linkedin.com' in url:
                    linkedin = url
                    break
            if name:
                persons.append({
                    "name": name, "role": role, "linkedin": linkedin,
                    "email": email.replace('mailto:', '') if email else '',
                    "confidence": "high", "strategy": "json_ld"
                })
        for key in ('employee', 'member', 'employees', 'founder', 'alumni'):
            nested = item.get(key, [])
            if isinstance(nested, dict):
                nested = [nested]
            if isinstance(nested, list):
                for sub in nested:
                    self._extract_jsonld_persons(sub, persons)

    async def strategy3_linkedin_page(self, session: aiohttp.ClientSession,
                                       linkedin_urls: List[str]) -> List[Dict]:
        persons = []
        profile_urls = [u for u in linkedin_urls if '/in/' in u]
        for url in profile_urls[:10]:
            try:
                async with session.get(url, headers={"User-Agent": USER_AGENT}, timeout=10,
                                       ssl=self._get_ssl_context(), allow_redirects=True) as resp:
                    if resp.status != 200:
                        continue
                    text = await resp.text()
                    li_soup = BeautifulSoup(text, 'html.parser')
                    name = ""
                    role = ""
                    og_title = li_soup.find('meta', property='og:title')
                    if og_title and og_title.get('content'):
                        parts = og_title['content'].split(' - ')
                        if parts:
                            name = parts[0].strip()
                        if len(parts) > 1:
                            role = parts[1].strip()
                    if not name:
                        title_tag = li_soup.find('title')
                        if title_tag and title_tag.string:
                            parts = title_tag.string.split(' - ')
                            if parts:
                                name = parts[0].strip()
                            if len(parts) > 1:
                                role = parts[1].strip()
                    if name and '| LinkedIn' in name:
                        name = name.replace('| LinkedIn', '').strip()
                    if role and '| LinkedIn' in role:
                        role = role.replace('| LinkedIn', '').strip()
                    if name and name.lower() != 'linkedin':
                        persons.append({
                            "name": name, "role": role, "linkedin": url,
                            "email": "", "confidence": "medium", "strategy": "linkedin_page"
                        })
            except Exception as e:
                logger.debug(f"Strategy 3 failed for {url}: {e}")
                continue
            await asyncio.sleep(0.5)
        return persons

    def strategy4_email_parsing(self, emails: List[str], site_domain: str = "") -> List[Dict]:
        persons = []
        for email in emails:
            local_part = email.split('@')[0]
            if local_part.lower() in GENERIC_EMAIL_PREFIXES:
                continue
            if RE_MACHINE_EMAIL_LOCAL.fullmatch(local_part):
                continue
            parts = re.split(r'[._\-]', local_part)
            if len(parts) >= 2:
                name_parts = [p.capitalize() for p in parts if re.fullmatch(r'[a-zA-Z]{2,}', p)]
                if len(name_parts) < 2:
                    continue
                name = ' '.join(name_parts)
            else:
                continue
            persons.append({
                "name": name, "role": "", "linkedin": "",
                "email": email, "confidence": "medium", "strategy": "email_parsing"
            })
        return persons

    def strategy5_url_slug(self, linkedin_urls: List[str]) -> List[Dict]:
        persons = []
        seen = set()
        for url in linkedin_urls:
            if '/in/' not in url:
                continue
            match = re.search(r'/in/([a-zA-Z0-9\-]+)', url)
            if not match:
                continue
            slug = match.group(1)
            if slug in seen:
                continue
            seen.add(slug)
            parts = slug.split('-')
            name_parts = []
            for p in parts:
                if re.match(r'^[a-zA-Z]+$', p) and len(p) > 1:
                    name_parts.append(p.capitalize())
                elif re.match(r'^[a-zA-Z]+$', p) and len(p) == 1:
                    name_parts.append(p.upper())
                elif re.search(r'\d', p):
                    break
            if len(name_parts) >= 2:
                name = ' '.join(name_parts)
                persons.append({
                    "name": name, "role": "", "linkedin": url,
                    "email": "", "confidence": "low", "strategy": "url_slug"
                })
        return persons

    async def extract_persons(self, session: aiohttp.ClientSession,
                               page_artifacts: List[PageArtifact],
                               linkedin_urls: List[str],
                               emails: List[str],
                               site_domain: str = "") -> List[Dict]:
        all_persons = []
        container_count = 0
        jsonld_count = 0

        for artifact in page_artifacts:
            if not artifact.soup:
                continue
            extracted = self.extract_from_page_artifact(artifact)
            container_count += len(extracted)
            all_persons.extend(extracted)

            jsonld_people = self.strategy2_json_ld(artifact.soup)
            jsonld_count += len(jsonld_people)
            all_persons.extend(jsonld_people)

        logger.info(f"  Strategy 1 (Container extraction): {container_count} persons")
        logger.info(f"  Strategy 2 (JSON-LD): {jsonld_count} persons")

        found_linkedin_urls = {p['linkedin'] for p in all_persons if p.get('linkedin')}
        remaining_urls = [u for u in linkedin_urls if u not in found_linkedin_urls and '/in/' in u]
        if remaining_urls:
            s3 = await self.strategy3_linkedin_page(session, remaining_urls)
            all_persons.extend(s3)
            logger.info(f"  Strategy 3 (LinkedIn page): {len(s3)} persons")
        s4 = self.strategy4_email_parsing(emails, site_domain)
        all_persons.extend(s4)
        logger.info(f"  Strategy 4 (Email parsing): {len(s4)} persons")
        found_linkedin_urls = {p['linkedin'] for p in all_persons if p.get('linkedin')}
        remaining_urls = [u for u in linkedin_urls if u not in found_linkedin_urls and '/in/' in u]
        if remaining_urls:
            s5 = self.strategy5_url_slug(remaining_urls)
            all_persons.extend(s5)
            logger.info(f"  Strategy 5 (URL slug): {len(s5)} persons")
        return self._dedupe_persons(all_persons)

    def _dedupe_persons(self, persons: List[Dict]) -> List[Dict]:
        confidence_rank = {"primary": 4, "high": 4, "secondary": 3, "medium": 2, "low": 1}

        def normalize_name(name: str) -> str:
            return re.sub(r'\s+', ' ', (name or '').strip().lower())

        def linkedin_key(record: Dict) -> str:
            linkedin = self._clean_linkedin_url(record.get('linkedin', '')).lower()
            return linkedin if '/in/' in linkedin else ""

        def email_key(record: Dict) -> str:
            return self._email_identity_key(record.get('email', ''))

        def merge_into(existing: Dict, incoming: Dict):
            for field in ('name', 'role', 'email', 'linkedin', 'bio', 'source_url', 'evidence'):
                if not existing.get(field) and incoming.get(field):
                    existing[field] = incoming[field]
            if confidence_rank.get(incoming.get('confidence', 'low'), 0) > \
               confidence_rank.get(existing.get('confidence', 'low'), 0):
                existing['confidence'] = incoming.get('confidence', existing.get('confidence'))
                existing['strategy'] = incoming.get('strategy', existing.get('strategy'))

        records: List[Dict] = []
        key_index: Dict[str, Dict] = {}

        for person in persons:
            if not person.get('name'):
                continue
            keys = [linkedin_key(person), email_key(person), normalize_name(person.get('name', ''))]
            keys = [key for key in keys if key]
            existing = next((key_index[key] for key in keys if key in key_index), None)
            if existing is None:
                existing = dict(person)
                records.append(existing)
            else:
                merge_into(existing, person)
            for key in keys:
                key_index[key] = existing

        final_by_name: Dict[str, Dict] = {}
        for record in records:
            name_key = normalize_name(record.get('name', ''))
            if not name_key:
                continue
            existing = final_by_name.get(name_key)
            if existing is None:
                final_by_name[name_key] = record
            else:
                merge_into(existing, record)

        return list(final_by_name.values())


# ─── Upload / process functions ───────────────────────────────────────────────

async def upload_to_google(session: aiohttp.ClientSession, website: str, links: List[str], emails: List[str],
                           tab: str = None, row: int = None):
    if not links and not emails:
        logger.info(f"Skipping upload for {website}: No links or emails found.")
        return
    payload = {
        "input_website": website,
        "linkedin_urls": links,
        "emails": emails
    }
    if tab and row:
        payload["tab"] = tab
        payload["row"] = row
    await GoogleClient.post_async(session, payload)


async def process_single_site(finder: LinkedInFinder, google_session: aiohttp.ClientSession,
                               site_info: dict, semaphore: asyncio.Semaphore,
                               person_extractor: PersonExtractor = None) -> dict:
    async with semaphore:
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
            links, emails, page_artifacts = await finder.find_linkedin_links(url)
            logger.info(f"Done {url} -> {len(links)} LinkedIn, {len(emails)} emails")

            persons = []
            if person_extractor and (links or emails):
                persons = await person_extractor.extract_persons(
                    google_session, page_artifacts, links, emails,
                    site_domain=urllib.parse.urlparse(url).netloc
                )
                logger.info(f"  Extracted {len(persons)} persons from {url}")

            company_linkedin = ""
            for li_url in links:
                if '/company/' in li_url:
                    company_linkedin = li_url
                    break

            result = {
                "input_website": url,
                "name": name,
                "linkedin_urls": links,
                "company_linkedin": company_linkedin,
                "emails": emails,
                "persons": persons,
            }

            if tab and row:
                result["tab"] = tab
                result["row"] = row

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
    return await GoogleClient.fetch_urls(session)


async def main_from_sheet(batch_size: int = 50, dry_run: bool = False):
    start_time = time.time()
    logger.info("Fetching URLs from source sheet (Alpha/Beta/Gamma)...")

    async with aiohttp.ClientSession() as session:
        url_list = await GoogleClient.fetch_source_urls(session)
        if not url_list:
            logger.info("No URLs from source sheet, trying legacy endpoint...")
            url_list = await fetch_urls_from_sheet(session)
        if not url_list:
            logger.info("No unprocessed URLs found.")
            return

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

        companies_payload = []
        error_updates = []
        done_updates = []
        for result in all_results:
            if result.get("error"):
                if result.get("tab") and result.get("row"):
                    error_updates.append({"tab": result["tab"], "row": result["row"], "status": "error"})
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
                    {"name": p.get("name", ""), "role": p.get("role", ""),
                     "email": p.get("email", ""), "linkedin": p.get("linkedin", "")}
                    for p in result.get("persons", [])
                ]
            }
            companies_payload.append(company_entry)
            if result.get("tab") and result.get("row"):
                done_updates.append({"tab": result["tab"], "row": result["row"], "status": "done"})

        if not dry_run:
            # Always send error updates regardless of output write success
            if error_updates:
                await GoogleClient.mark_source_processed(session, error_updates)
                logger.info(f"Marked {len(error_updates)} error rows in source sheet")

            if companies_payload:
                success = await GoogleClient.post_to_output(session, companies_payload)
                if success:
                    logger.info(f"Posted {len(companies_payload)} companies to Employee_db")
                    if done_updates:
                        await GoogleClient.mark_source_processed(session, done_updates)
                        logger.info(f"Marked {len(done_updates)} rows as done in source sheet")
                else:
                    logger.error("Failed to post to Employee_db — source rows NOT marked done")
        else:
            logger.info(f"DRY RUN: Would post {len(companies_payload)} companies to Employee_db")

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
        batch_size = int(args[1]) if len(args) > 1 else 5
        asyncio.run(main_from_sheet(batch_size, dry_run=dry_run))
    else:
        test_urls = [
            "https://www.assetpartners.nl/",
            "https://www.famadvocaten.nl/nl/",
            "https://www.axento.nl/"
        ]
        asyncio.run(main(test_urls))
