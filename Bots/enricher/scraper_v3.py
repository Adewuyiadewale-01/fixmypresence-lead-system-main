import asyncio
import fcntl
import hashlib
import os
import re
import signal
import time
import urllib.parse
import html
import json
import logging
import ssl
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Set, Dict, Tuple
from datetime import datetime
import aiohttp
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
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
SITE_PROCESS_TIMEOUT = 180  # Hard ceiling per site so one slow site doesn't stall the batch
RECOVERY_MODE_TIMEOUT_THRESHOLD = 2  # Repeated Playwright timeouts before site-level recovery mode

# ─── Anti-block constants ─────────────────────────────────────────────────────
DOMAIN_REQUEST_DELAY = 0.8          # Min seconds between requests to the same domain
DOMAIN_BLOCK_COOLDOWN = 30.0        # Seconds to cool down after a block is detected
DOMAIN_MAX_FAILURES = 3             # Consecutive failures before the domain is abandoned for this run
REDUCED_EXPANSION_AFTER_FAILURES = 1  # After this many failures, cut subpage/L2 limits in half

# ─── Write chunking ──────────────────────────────────────────────────────────
OUTPUT_WRITE_CHUNK_SIZE = 3         # Companies per output webhook call

# ─── Retry policy ─────────────────────────────────────────────────────────────
TRANSIENT_RETRY_MAX = 3             # Max retries for transient webhook/network failures
TRANSIENT_RETRY_BASE_DELAY = 1.0    # Base delay (seconds) for exponential backoff

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

PERSON_NAME_BLOCKLIST = {
    'privacy', 'statement', 'policy', 'cookie', 'cookies', 'voorwaarden',
    'disclaimer', 'advocaten', 'lawyers', 'attorneys', 'team', 'contact',
    'profiel', 'profile',
}

LEGAL_NOISE_TERMS = {
    'privacy', 'statement', 'policy', 'cookie', 'cookies', 'terms',
    'voorwaarden', 'disclaimer', 'copyright', 'all rights reserved',
    'sitemap', 'algemene voorwaarden', 'privacy statement',
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
    "/ons-team", "/our-team", "/social", "/connect", "/footer", "/imprint", "/legal",
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

AUTH_PATH_SEGMENTS = {
    "dashboard", "account", "login", "logout", "signin", "signup",
    "register", "settings", "orders", "order", "bestellingen",
    "bestelling", "subscription", "subscriptions", "abonnement",
    "abonnementen", "checkout", "cart", "winkelmand",
}

ARTICLE_PATH_SEGMENTS = {
    "blog", "news", "nieuws", "insights", "artikelen", "article",
    "articles", "category", "tag", "tags", "author",
}

PROFILE_PATH_SEGMENTS = {
    "team", "people", "person", "personen", "medewerkers",
    "medewerker", "our-people", "onze-mensen", "leadership",
    "about-us", "over-ons", "organisatie", "staff",
}

AUTH_FAMILY_PRIORITY = (
    "dashboard", "account", "settings", "orders", "subscription",
    "checkout", "cart", "login", "logout", "signin", "signup",
    "register",
)


# ─── Block / CAPTCHA detection patterns ───────────────────────────────────────
BLOCK_PAGE_SIGNATURES = [
    "access denied", "access blocked", "403 forbidden",
    "you have been blocked", "you are blocked",
    "please verify you are a human", "verify you are human",
    "captcha", "recaptcha", "hcaptcha",
    "cloudflare", "checking your browser",
    "just a moment", "attention required",
    "ddos protection by", "protection by cloudflare",
    "sucuri website firewall",
    "bot detection", "automated access",
    "too many requests", "rate limit",
]


def _is_block_page(text: str, status_code: int = 200) -> bool:
    """Detect if a response is a block/CAPTCHA/WAF page."""
    if status_code in (403, 429, 503):
        return True
    if not text:
        return False
    lower = text[:3000].lower()
    matches = sum(1 for sig in BLOCK_PAGE_SIGNATURES if sig in lower)
    if matches >= 2:
        return True
    if status_code != 200 and matches >= 1:
        return True
    return False


class DomainPacer:
    """Per-domain request pacing and failure tracking for anti-block control."""

    def __init__(self):
        self._last_request: Dict[str, float] = {}
        self._failure_count: Dict[str, int] = {}
        self._blocked: Set[str] = set()
        self._lock = asyncio.Lock()

    def _domain_key(self, url: str) -> str:
        try:
            return _normalize_domain(urllib.parse.urlparse(url).netloc)
        except Exception:
            return ""

    async def pace(self, url: str):
        """Wait if needed to respect per-domain delay."""
        domain = self._domain_key(url)
        if not domain:
            return
        async with self._lock:
            last = self._last_request.get(domain, 0.0)
            now = time.time()
            wait = DOMAIN_REQUEST_DELAY - (now - last)
            if domain in self._blocked:
                wait = max(wait, DOMAIN_BLOCK_COOLDOWN - (now - last))
        if wait > 0:
            await asyncio.sleep(wait)
        async with self._lock:
            self._last_request[domain] = time.time()

    async def record_success(self, url: str):
        domain = self._domain_key(url)
        if not domain:
            return
        async with self._lock:
            self._failure_count[domain] = 0
            self._blocked.discard(domain)

    async def record_failure(self, url: str, is_block: bool = False):
        domain = self._domain_key(url)
        if not domain:
            return
        async with self._lock:
            self._failure_count[domain] = self._failure_count.get(domain, 0) + 1
            if is_block:
                self._blocked.add(domain)
                self._last_request[domain] = time.time()

    async def should_skip(self, url: str) -> bool:
        """True if this domain has been abandoned due to too many failures."""
        domain = self._domain_key(url)
        if not domain:
            return False
        async with self._lock:
            return self._failure_count.get(domain, 0) >= DOMAIN_MAX_FAILURES

    async def should_reduce_expansion(self, url: str) -> bool:
        """True if this domain has had enough failures to warrant reduced crawling."""
        domain = self._domain_key(url)
        if not domain:
            return False
        async with self._lock:
            return self._failure_count.get(domain, 0) >= REDUCED_EXPANSION_AFTER_FAILURES

    def get_stats(self) -> Dict:
        return {
            "blocked_domains": list(self._blocked),
            "failure_counts": dict(self._failure_count),
        }


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


def _normalize_domain(domain: str) -> str:
    domain = (domain or "").strip().lower()
    if not domain:
        return ""
    domain = domain.split(":", 1)[0].rstrip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _same_site_family(domain_a: str, domain_b: str) -> bool:
    a = _normalize_domain(domain_a)
    b = _normalize_domain(domain_b)
    if not a or not b:
        return False
    if a == b:
        return True
    return a.endswith("." + b) or b.endswith("." + a)


_GENERIC_REGISTRABLE_LABELS = {
    "co", "com", "org", "net", "gov", "edu", "ac",
}


def _registrable_brand_label(domain: str) -> str:
    normalized = _normalize_domain(domain)
    if not normalized:
        return ""
    parts = normalized.split(".")
    if len(parts) < 2:
        return normalized
    label_index = -2
    if len(parts) >= 3 and parts[-2] in _GENERIC_REGISTRABLE_LABELS:
        label_index = -3
    label = parts[label_index].strip("-")
    if len(label) < 4:
        return ""
    return label


def _same_brand_homepage_redirect(domain_a: str, domain_b: str) -> bool:
    """Allow careful homepage adoption across TLDs for the same brand.

    This is intentionally narrower than _same_site_family and should only be
    used when choosing the canonical homepage origin after an initial redirect.
    """
    if _same_site_family(domain_a, domain_b):
        return True
    a = _registrable_brand_label(domain_a)
    b = _registrable_brand_label(domain_b)
    return bool(a and b and a == b)


def _is_people_page_url(url: str) -> bool:
    segments = _url_segments(url)
    if not segments:
        return False
    for seg in segments:
        base = seg.split(".")[0]
        if base in INTERNAL_LINK_KEYWORDS:
            return True
        if any(keyword in base for keyword in ("team", "people", "staff", "board", "management", "employee")):
            return True
    return False


def _playwright_result_is_sparse(result: Dict) -> bool:
    soup = result.get("soup")
    final_url = (result.get("final_url") or "").strip().lower()
    if final_url == "about:blank":
        return True
    if soup is None:
        return True
    text = soup.get_text(" ", strip=True)
    text_len = len(text)
    internal_count = len(result.get("internal_links") or [])
    linkedin_count = len(result.get("linkedin") or [])
    email_count = len(result.get("emails") or [])
    return text_len < 200 and internal_count < 3 and linkedin_count == 0 and email_count == 0


def _recovery_filter_urls(urls: List[str]) -> List[str]:
    """In timeout recovery mode, keep only obvious people pages."""
    filtered = [url for url in urls if _is_people_page_url(url)]
    return dedupe_keep_order(filtered)


def _url_segments(url: str) -> List[str]:
    try:
        parsed = urllib.parse.urlparse(url)
        path = urllib.parse.unquote(parsed.path or "")
    except Exception:
        return []
    return [seg.lower() for seg in path.split('/') if seg]


def _path_has_auth_segment(url: str) -> bool:
    for seg in _url_segments(url):
        base = seg.split('.')[0]
        if base in AUTH_PATH_SEGMENTS:
            return True
        if any(base.startswith(prefix) for prefix in ("bestelling", "abonnement")):
            return True
    return False


def _auth_prefix_family(url: str) -> str:
    segments = _url_segments(url)
    for family in AUTH_FAMILY_PRIORITY:
        for seg in segments:
            base = seg.split('.')[0]
            if base == family or base.startswith(family):
                return family
    return ""


def _path_has_article_signal(url: str) -> bool:
    segments = _url_segments(url)
    if any(seg in ARTICLE_PATH_SEGMENTS for seg in segments):
        return True
    try:
        path = urllib.parse.unquote(urllib.parse.urlparse(url).path or "").strip('/')
    except Exception:
        return False
    if re.search(r'/(19|20)\d{2}/\d{1,2}(/|$)', '/' + path):
        return True
    if segments:
        last = segments[-1]
        word_count = len([w for w in last.split('-') if w])
        if word_count >= 5:
            return True
    return False


def _is_url_hard_auth_candidate(url: str) -> bool:
    """Very conservative URL-only hard reject for auth/dashboard utility pages."""
    return _path_has_auth_segment(url)


def _is_profile_like_href(href: str, base_url: str, anchor_text: str = "") -> bool:
    try:
        resolved = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(resolved)
        base = urllib.parse.urlparse(base_url)
        if not _same_site_family(parsed.netloc, base.netloc):
            return False
    except Exception:
        return False

    segments = _url_segments(resolved)
    if not segments:
        return False
    if any(seg in PROFILE_PATH_SEGMENTS for seg in segments):
        return True
    if any(seg in ARTICLE_PATH_SEGMENTS for seg in segments):
        return False
    last = urllib.parse.unquote(segments[-1]).replace('-', ' ').strip()
    if _looks_like_person_name_static(anchor_text):
        return True
    if _looks_like_person_name_static(last):
        return True
    last_tokens = _normalize_tokens(last)
    if len(last_tokens) >= 2 and all(token.isalpha() for token in last_tokens):
        return True
    return False


def _page_people_evidence(soup: BeautifulSoup, url: str, classification: Optional[Dict] = None) -> Dict[str, int]:
    """Collect lightweight page-level people evidence counts."""
    personal_li = 0
    personal_email = 0
    profile_link_count = 0
    localized_profile_cluster = 0
    parent_counts: Dict[int, int] = {}

    for a in soup.find_all('a', href=True):
        href = a.get('href') or ''
        if _is_in_global_zone(a):
            continue
        if _is_personal_linkedin(href):
            personal_li += 1
        if href.lower().startswith(('mailto:', 'mail:')):
            email = re.sub(r'^ma?ilto?:', '', href, flags=re.I)
            if _is_personal_email(email):
                personal_email += 1
        if _is_profile_like_href(href, url, a.get_text(" ", strip=True)):
            profile_link_count += 1
            parent = a.parent
            if parent:
                pid = id(parent)
                parent_counts[pid] = parent_counts.get(pid, 0) + 1

    if parent_counts:
        localized_profile_cluster = max(parent_counts.values())

    signals = set(classification.get("signals", [])) if classification else set()
    has_person_jsonld = any(sig in signals for sig in ("jsonld_person", "jsonld_person_list"))

    return {
        "personal_linkedin_count": personal_li,
        "personal_email_count": personal_email,
        "profile_link_count": profile_link_count,
        "localized_profile_cluster": localized_profile_cluster,
        "has_person_jsonld": 1 if has_person_jsonld else 0,
    }


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
        if _path_has_article_signal(self.url):
            total -= 5
        return total


class CandidatePool:
    """Collects URLs from all discovery sources, deduplicates, scores, and ranks."""

    def __init__(self, base_url: str):
        parsed = urllib.parse.urlparse(base_url)
        self.base = f"{parsed.scheme}://{parsed.netloc}"
        self.base_domain = _normalize_domain(parsed.netloc)
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
            domain = _normalize_domain(parsed.netloc)
            if domain and not _same_site_family(domain, self.base_domain):
                return None
        except Exception:
            return None
        return url

    def add(self, url: str, source: str):
        normalized = self._normalize(url)
        if not normalized:
            return
        if _is_url_hard_auth_candidate(normalized):
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
        final_urls = []
        auth_family_counts: Dict[str, int] = {}
        for candidate in ranked:
            family = _auth_prefix_family(candidate.url)
            if family:
                if auth_family_counts.get(family, 0) >= 1:
                    continue
                auth_family_counts[family] = auth_family_counts.get(family, 0) + 1
            final_urls.append(candidate.url)
            if len(final_urls) >= limit:
                break
        return final_urls

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
    classification: Optional[Dict] = None  # Output of classify_page()
    personal_linkedin_count: int = 0
    personal_email_count: int = 0
    profile_link_count: int = 0
    signal_priority: int = 0


# ─── Lightweight page classifier ─────────────────────────────────────────────
# Shallow, coarse signals only. Must NOT do ancestor walk-up, card extraction,
# peer promotion, or sibling sweep. Used after fetch/render to gate level-2
# expansion — never blocks the initial fetch of a candidate page.

_AUTH_NAV_KEYWORDS = {
    "dashboard", "account", "settings", "logout", "log out", "sign out",
    "my orders", "my account", "subscriptions", "api keys", "api-keys",
    "billing", "payment", "password", "profile settings",
}

_ARTICLE_LD_TYPES = {"BlogPosting", "NewsArticle", "Article", "TechArticle", "Report"}


def classify_page(soup: BeautifulSoup, url: str = "") -> Dict:
    """Classify a fetched page into people_hint / article_score / auth_score.

    Returns dict with:
        people_hint: int   — coarse signal that this page contains people info
        article_score: int — signal that this is an article/blog page
        auth_score: int    — signal that this is a dashboard/account/auth page
        signals: list[str] — explainability tags for debugging
    """
    people_hint = 0
    article_score = 0
    auth_score = 0
    signals: List[str] = []

    # ── People hints (cheap, page-level only) ──

    # JSON-LD Person / ItemList of persons
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            import json as _json
            data = _json.loads(script.string or '{}')
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_type = item.get('@type', '')
                if isinstance(item_type, list):
                    item_type = ' '.join(item_type)
                if 'Person' in item_type:
                    people_hint += 2
                    signals.append("jsonld_person")
                # Organization with employee list
                for key in ('employee', 'employees', 'member', 'founder'):
                    nested = item.get(key, [])
                    if isinstance(nested, dict):
                        nested = [nested]
                    if isinstance(nested, list) and len(nested) >= 2:
                        people_hint += 2
                        signals.append("jsonld_person_list")
                        break
        except Exception:
            continue

    evidence = _page_people_evidence(soup, url)
    personal_li_count = evidence["personal_linkedin_count"]
    personal_email_count = evidence["personal_email_count"]
    profile_link_count = evidence["profile_link_count"]
    localized_profile_cluster = evidence["localized_profile_cluster"]

    if personal_li_count >= 1:
        people_hint += 1
        signals.append(f"personal_linkedin_x{personal_li_count}")
    if personal_email_count >= 1:
        people_hint += 1
        signals.append(f"personal_emails_x{personal_email_count}")
    if personal_li_count >= 1 and personal_email_count >= 1:
        people_hint += 1
        signals.append("mixed_personal_signals")
    if localized_profile_cluster >= 2:
        people_hint += 2
        signals.append(f"profile_link_cluster_x{localized_profile_cluster}")
    elif profile_link_count >= 2:
        people_hint += 1
        signals.append(f"profile_links_x{profile_link_count}")

    # ── Article score ──

    if _path_has_article_signal(url):
        article_score += 2
        signals.append("article_url")

    if soup.find('article'):
        article_score += 2
        signals.append("article_tag")

    # JSON-LD article types
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            import json as _json
            data = _json.loads(script.string or '{}')
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_type = item.get('@type', '')
                if isinstance(item_type, list):
                    item_type = ' '.join(item_type)
                if any(t in item_type for t in _ARTICLE_LD_TYPES):
                    article_score += 2
                    signals.append("jsonld_article")
                    break
        except Exception:
            continue

    # datePublished meta
    if soup.find('meta', attrs={'property': 'article:published_time'}) or \
       soup.find('time', attrs={'datetime': True}):
        article_score += 1
        signals.append("date_published")

    # Byline / author patterns
    author_el = soup.find(class_=re.compile(r'author|byline', re.I))
    if author_el:
        article_score += 1
        signals.append("byline")

    if soup.find(class_=re.compile(r'tag|category|related-post|related', re.I)) or \
       soup.find(id=re.compile(r'tag|category|related-post|related', re.I)):
        article_score += 1
        signals.append("article_taxonomy")

    # ── Auth / dashboard score ──

    # Password fields
    if soup.find('input', attrs={'type': 'password'}):
        auth_score += 3
        signals.append("password_field")

    # Account update forms
    for form in soup.find_all('form'):
        action = (form.get('action') or '').lower()
        if any(kw in action for kw in ['account', 'login', 'signin', 'signup',
                                         'register', 'password', 'settings']):
            auth_score += 2
            signals.append("auth_form")
            break

    # Logout / account nav links
    auth_link_count = 0
    for a in soup.find_all('a', href=True):
        href = (a.get('href') or '').lower()
        text = (a.get_text(strip=True) or '').lower()
        combined = href + " " + text
        if any(kw in combined for kw in _AUTH_NAV_KEYWORDS):
            auth_link_count += 1
    if auth_link_count >= 2:
        auth_score += 2
        signals.append(f"auth_nav_links_x{auth_link_count}")

    auth_heading_button_count = 0
    for node in soup.find_all(['h1', 'h2', 'h3', 'button']):
        text = node.get_text(" ", strip=True).lower()
        if any(kw in text for kw in _AUTH_NAV_KEYWORDS):
            auth_heading_button_count += 1
    if auth_heading_button_count >= 1:
        auth_score += 1
        signals.append("auth_heading_button")

    # URL-based penalty (very conservative, language-agnostic only)
    url_lower = url.lower()
    if _path_has_auth_segment(url_lower):
        auth_score += 1
        signals.append("auth_url_segment")

    return {
        "people_hint": people_hint,
        "article_score": article_score,
        "auth_score": auth_score,
        "signals": signals,
    }


def is_hard_auth_page(url: str, soup: BeautifulSoup, classification: Optional[Dict] = None) -> bool:
    """Near-certain auth/dashboard reject using path + UI evidence."""
    classification = classification or classify_page(soup, url)
    signals = set(classification.get("signals", []))
    if "password_field" in signals:
        return True

    has_auth_url = _path_has_auth_segment(url)
    has_logout = False
    auth_nav_cluster = 0
    for a in soup.find_all('a', href=True):
        combined = ((a.get('href') or '') + " " + a.get_text(" ", strip=True)).lower()
        if any(kw in combined for kw in ('logout', 'sign out', 'uitloggen')):
            has_logout = True
        if any(kw in combined for kw in _AUTH_NAV_KEYWORDS):
            auth_nav_cluster += 1

    has_auth_form = "auth_form" in signals
    heading_button = "auth_heading_button" in signals
    if has_auth_url and (has_logout or auth_nav_cluster >= 3 or has_auth_form):
        return True

    auth_ui_signals = sum([
        1 if has_logout else 0,
        1 if auth_nav_cluster >= 3 else 0,
        1 if has_auth_form else 0,
        1 if heading_button else 0,
    ])
    return auth_ui_signals >= 2 and classification.get("people_hint", 0) == 0


def is_soft_article_page(url: str, soup: BeautifulSoup, classification: Optional[Dict] = None) -> bool:
    """Return True for article/blog pages that lack meaningful people evidence."""
    classification = classification or classify_page(soup, url)
    evidence = _page_people_evidence(soup, url, classification)
    has_people_evidence = (
        evidence["personal_linkedin_count"] >= 1
        or evidence["personal_email_count"] >= 1
        or evidence["localized_profile_cluster"] >= 2
        or evidence["profile_link_count"] >= 2
        or evidence["has_person_jsonld"] >= 1
    )
    if has_people_evidence:
        return False
    article_score = classification.get("article_score", 0)
    if article_score >= 3:
        return True
    return article_score >= 2 and _path_has_article_signal(url)


def should_suppress_level2(classification: Dict, soup: Optional[BeautifulSoup] = None, url: str = "") -> bool:
    """Return True if the page classification indicates level-2 expansion
    should be suppressed (auth/article dominates people)."""
    if not classification:
        return False
    if soup is not None:
        if is_hard_auth_page(url, soup, classification):
            return True
        if is_soft_article_page(url, soup, classification):
            return True
        return False
    ph = classification.get("people_hint", 0)
    if classification.get("auth_score", 0) >= 3 and ph == 0:
        return True
    if classification.get("article_score", 0) >= 3 and ph == 0:
        return True
    return False


# ─── Structural fingerprint for card containers ──────────────────────────────

def _container_shape(container) -> tuple:
    """Return a structural fingerprint for a container element.

    Uses only DOM structure, not text content. Fingerprint components:
    - direct child tag names (capped at 8)
    - descendant count bucket (0-5, 6-15, 16-30, 31+)
    - short-leaf count bucket
    - flags for image, anchor, mailto, linkedin, tel
    """
    # Direct child tags
    child_tags = []
    for child in container.children:
        tag_name = getattr(child, 'name', None)
        if tag_name:
            child_tags.append(tag_name)
    child_tags = tuple(child_tags[:8])

    # Descendant count bucket
    desc_count = len(container.find_all(True))
    if desc_count <= 5:
        desc_bucket = 'xs'
    elif desc_count <= 15:
        desc_bucket = 'sm'
    elif desc_count <= 30:
        desc_bucket = 'md'
    else:
        desc_bucket = 'lg'

    # Short leaf count
    short_leaves = 0
    for tag in container.find_all(True):
        if tag.name in {'script', 'style', 'svg', 'path'}:
            continue
        if not any(getattr(c, 'name', None) for c in tag.children):
            text = tag.get_text(strip=True)
            if 2 <= len(text) <= 120:
                short_leaves += 1
    if short_leaves <= 2:
        leaf_bucket = 'few'
    elif short_leaves <= 5:
        leaf_bucket = 'some'
    else:
        leaf_bucket = 'many'

    # Flags
    has_img = bool(container.find('img'))
    has_anchor = bool(container.find('a', href=True))
    has_mailto = bool(container.find('a', href=re.compile(r'^mailto:', re.I)))
    has_linkedin = bool(container.find('a', href=re.compile(r'linkedin\.com', re.I)))
    has_tel = bool(container.find('a', href=re.compile(r'^tel:', re.I)))

    return (child_tags, desc_bucket, leaf_bucket, has_img, has_anchor,
            has_mailto, has_linkedin, has_tel)


def _shapes_match(shape_a: tuple, shape_b: tuple) -> bool:
    """Return True if two container shapes are structurally similar enough
    to be considered peers in a people section."""
    if not shape_a or not shape_b:
        return False
    tags_a, desc_a, leaf_a = shape_a[:3]
    tags_b, desc_b, leaf_b = shape_b[:3]

    # Same descendant bucket
    if desc_a != desc_b:
        return False

    # Child tags: intersection-over-union >= 60%
    if not tags_a or not tags_b:
        return tags_a == tags_b
    set_a = set(tags_a)
    set_b = set(tags_b)
    if not set_a and not set_b:
        return True
    iou = len(set_a & set_b) / max(len(set_a | set_b), 1)
    if iou < 0.6:
        return False

    return True


# ─── Name-node positional path (for sibling sweep corroboration) ─────────────

def _name_node_path(container, name_node) -> Optional[tuple]:
    """Return the DOM path from container root to name_node as a tuple of tag
    names. Used for positional corroboration in sibling sweep: a sibling
    container's name-like text must be at the same structural position."""
    if name_node is None or container is None:
        return None
    path = []
    current = name_node
    while current and current != container:
        if getattr(current, 'name', None):
            path.append(current.name)
        current = getattr(current, 'parent', None)
    if current != container:
        return None  # name_node is not a descendant of container
    return tuple(reversed(path))


def _find_name_at_position(container, target_path: tuple, role_keywords: List[str]) -> Optional:
    """Find a name-like leaf node at the same structural position in a
    sibling container. Returns the node or None."""
    if not target_path:
        return None

    # Walk down the container following the tag path
    candidates = [container]
    for tag_name in target_path[:-1]:
        next_candidates = []
        for node in candidates:
            for child in node.children:
                if getattr(child, 'name', None) == tag_name:
                    next_candidates.append(child)
        candidates = next_candidates
        if not candidates:
            return None

    # At the final level, find the matching tag that contains a name-like text
    final_tag = target_path[-1]
    for node in candidates:
        for child in node.children:
            if getattr(child, 'name', None) == final_tag:
                text = child.get_text(" ", strip=True)
                combined = _split_combined_name_role_static(text, role_keywords)
                if (_looks_like_person_name_static(text) and not _matches_role_static(text, role_keywords)) or combined:
                    return child
    return None


def _path_prefix_similarity(path_a: Optional[tuple], path_b: Optional[tuple]) -> float:
    if not path_a or not path_b:
        return 0.0
    common = 0
    for a, b in zip(path_a, path_b):
        if a != b:
            break
        common += 1
    return common / max(len(path_a), len(path_b), 1)


def _split_combined_name_role_static(text: str, role_keywords: List[str]) -> Optional[Tuple[str, str]]:
    clean = re.sub(r'\s+', ' ', (text or '').strip())
    if not clean:
        return None
    if len(clean.split()) > 8:
        return None
    for sep in (" - ", " – ", " — ", " | "):
        if sep not in clean:
            continue
        left, right = clean.split(sep, 1)
        left = left.strip()
        right = right.strip()
        if not left or not right:
            continue
        if len(left.split()) < 2 or len(left.split()) > 5:
            continue
        right_words = right.split()
        if not 1 <= len(right_words) <= 5:
            continue
        if _looks_like_phone_text(right) or _is_legal_or_footer_text(right):
            continue
        if _looks_like_person_name_static(left) and (
            _matches_role_static(right, role_keywords) or not _looks_like_person_name_static(right)
        ):
            return left, right
    return None


def _looks_like_person_name_static(text: str) -> bool:
    """Standalone version of PersonExtractor._looks_like_person_name."""
    clean = re.sub(r'\s+', ' ', (text or '').strip())
    words = clean.split()
    if len(words) < 2 or len(words) > 5:
        return False
    tokens = set(_normalize_tokens(clean))
    if tokens & PERSON_NAME_BLOCKLIST:
        return False
    particles = {'de', 'van', 'den', 'der', 'het', 'ten', 'ter', 'von',
                  'du', 'le', 'la', 'di', 'el', 'al', 'bin', 'op'}
    capitalized = re.compile(r"^[A-Z\u00C0-\u024F][A-Za-z\u00C0-\u024F'\u2018\u2019\-]+$")
    cap_count = 0
    for word in words:
        if word.lower() in particles:
            continue
        if not capitalized.match(word):
            return False
        cap_count += 1
    return cap_count >= 2


def _matches_role_static(text: str, role_keywords: List[str]) -> bool:
    """Standalone version of PersonExtractor._matches_role."""
    t = (text or "").strip().lower()
    return any(t == kw or t.startswith(kw) or t.endswith(kw) or kw in t for kw in role_keywords)


# ─── Personal-signal normalization helpers ────────────────────────────────────

def _normalize_tokens(text: str) -> List[str]:
    """Normalize a name/slug into lowercase tokens for matching."""
    text = re.sub(r"[''\".,;:!?()]+", "", (text or "").strip().lower())
    return [t for t in re.split(r'[\s\-_]+', text) if len(t) > 1]


def _is_personal_linkedin(url: str) -> bool:
    """Return True if this is a personal /in/ LinkedIn URL."""
    url_lower = (url or "").lower()
    if '/in/' not in url_lower:
        return False
    if any(x in url_lower for x in ['/share', '/sharearticle', '/embed', '/pulse/']):
        return False
    return 'linkedin.com' in url_lower


def _is_personal_email(email: str) -> bool:
    """Return True if this email looks like a personal inbox (not generic)."""
    if not email:
        return False
    local = email.split('@')[0].lower()
    if local in GENERIC_EMAIL_PREFIXES or len(local) <= 2:
        return False
    if RE_MACHINE_EMAIL_LOCAL.fullmatch(local):
        return False
    return True


def _match_name_to_signal(name_text: str, email: str = "", linkedin_url: str = "") -> str:
    """Match a candidate name against email/linkedin tokens.

    Returns: 'strong', 'medium', 'weak', or 'none'.
    """
    name_tokens = _normalize_tokens(name_text)
    if not name_tokens or len(name_tokens) < 2:
        return "none"

    # Build signal tokens from email and linkedin
    signal_tokens = set()
    email_local = ""
    if email and _is_personal_email(email):
        email_local = email.split('@')[0].lower()
        for t in re.split(r'[.\-_+]', email_local):
            if len(t) > 1:
                signal_tokens.add(t)

    li_slug = ""
    if linkedin_url and _is_personal_linkedin(linkedin_url):
        match = re.search(r'linkedin\.com/in/([^/?#]+)', linkedin_url, re.I)
        if match:
            li_slug = re.sub(r'-\d+$', '', match.group(1).lower())
            for t in li_slug.split('-'):
                if len(t) > 1:
                    signal_tokens.add(t)

    if not signal_tokens:
        return "none"

    # Exact token matches
    overlap_count = sum(1 for token in name_tokens if token in signal_tokens)
    first_match = name_tokens[0] in signal_tokens
    surname_match = name_tokens[-1] in signal_tokens
    joined_name = ''.join(name_tokens)
    signal_raws = [raw for raw in (email_local.replace('.', ''), li_slug.replace('-', '')) if raw]
    partial_token_match = any(
        raw and any(
            token in raw or raw in token or token.startswith(raw[:min(len(raw), 4)])
            for token in name_tokens if len(token) > 2
        )
        for raw in signal_raws if len(raw) > 2
    )

    # Strong: exact first+surname OR slug-contains-joined-name
    if overlap_count >= 2:
        return "strong"
    if first_match and surname_match:
        return "strong"
    if li_slug and (li_slug in joined_name or joined_name in li_slug):
        return "strong"
    if email_local and (email_local.replace('.', '') in joined_name or joined_name in email_local.replace('.', '')):
        return "strong"

    # Medium: first-name, surname-only, or strong partial token match
    if first_match and 2 <= len(name_tokens) <= 4:
        return "medium"
    if surname_match and 2 <= len(name_tokens) <= 4:
        return "medium"
    if partial_token_match and 2 <= len(name_tokens) <= 4:
        return "medium"

    # Weak: partial only
    any_match = any(t in signal_tokens for t in name_tokens)
    if any_match or partial_token_match:
        return "weak"

    return "none"


def _looks_like_phone_text(text: str) -> bool:
    clean = re.sub(r"[^\d+]", "", (text or "").strip())
    digit_count = sum(ch.isdigit() for ch in clean)
    if digit_count < 7 or digit_count > 15:
        return False
    raw = (text or "").strip()
    return bool(re.fullmatch(r"[\+\d\(\)\s.\-\/]+", raw))


def _is_legal_or_footer_text(text: str) -> bool:
    lower = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not lower:
        return False
    if any(term in lower for term in LEGAL_NOISE_TERMS):
        return True
    return False


# ─── Footer/header/nav detection ─────────────────────────────────────────────

_GLOBAL_ZONE_TAGS = {'header', 'footer', 'nav'}
_GLOBAL_ZONE_CLASSES = {'footer', 'header', 'nav', 'navbar', 'navigation',
                         'site-footer', 'site-header', 'global-footer',
                         'global-header', 'masthead', 'bottom-bar'}


def _is_in_global_zone(node) -> bool:
    """Return True if node is inside a header/footer/nav/global-contact zone."""
    current = node
    while current:
        tag_name = getattr(current, 'name', None)
        if tag_name in _GLOBAL_ZONE_TAGS:
            return True
        classes = current.get('class', []) if hasattr(current, 'get') else []
        if isinstance(classes, list):
            for cls in classes:
                if cls.lower() in _GLOBAL_ZONE_CLASSES:
                    return True
        current = getattr(current, 'parent', None)
    return False


# ─── Verified people section logic ───────────────────────────────────────────

def find_verified_sections(persons: List[Dict]) -> List[Dict]:
    """Identify verified people sections from extracted person records.

    A verified people section is a group of persons sharing the same
    parent_section_id where:
    - at least 2 person cards were extracted
    - at least 1 card is 'primary' confidence (backed by personal email/linkedin)

    Returns list of dicts: {parent_section_id, card_count, has_primary, persons}
    """
    by_section: Dict[str, List[Dict]] = {}
    for p in persons:
        section_id = p.get("parent_section_id", "unknown")
        by_section.setdefault(section_id, []).append(p)

    results = []
    for section_id, section_persons in by_section.items():
        has_primary = any(p.get("confidence") == "primary" for p in section_persons)
        results.append({
            "parent_section_id": section_id,
            "card_count": len(section_persons),
            "has_primary": has_primary,
            "persons": section_persons,
        })

    return results


class LinkedInFinder:
    def __init__(self, pacer: DomainPacer = None):
        self.browser = None
        self.playwright = None
        self._ssl_ctx = _make_ssl_context()
        self.pacer = pacer or DomainPacer()

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
        evidence = _page_people_evidence(soup, normalized_url)
        signal_priority = (
            evidence["personal_linkedin_count"] * 5
            + evidence["personal_email_count"] * 5
            + evidence["profile_link_count"] * 2
            + evidence["localized_profile_cluster"] * 3
            + evidence["has_person_jsonld"] * 4
        )
        return PageArtifact(
            url=normalized_url,
            soup=soup,
            linkedin_urls=set(linkedin_urls or set()),
            emails=set(emails or set()),
            personal_linkedin_count=evidence["personal_linkedin_count"],
            personal_email_count=evidence["personal_email_count"],
            profile_link_count=evidence["profile_link_count"],
            signal_priority=signal_priority,
        )

    @staticmethod
    def _artifact_soup_quality_key(artifact: Optional[PageArtifact]) -> Tuple[int, int, int, int, int]:
        if not artifact or not artifact.soup:
            return (-1, -1, -1, -1, -1)
        text_len = len(artifact.soup.get_text(" ", strip=True))
        anchor_count = len(artifact.soup.find_all("a", href=True))
        return (
            artifact.signal_priority,
            artifact.personal_linkedin_count + artifact.personal_email_count,
            artifact.profile_link_count,
            anchor_count,
            text_len,
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
        if LinkedInFinder._artifact_soup_quality_key(artifact) > LinkedInFinder._artifact_soup_quality_key(existing):
            existing.soup = artifact.soup
            existing.classification = artifact.classification
        existing.personal_linkedin_count = max(existing.personal_linkedin_count, artifact.personal_linkedin_count)
        existing.personal_email_count = max(existing.personal_email_count, artifact.personal_email_count)
        existing.profile_link_count = max(existing.profile_link_count, artifact.profile_link_count)
        existing.signal_priority = max(existing.signal_priority, artifact.signal_priority)

    @staticmethod
    def _artifact_priority_key(artifact: PageArtifact) -> Tuple[int, int, int, str]:
        return (
            artifact.signal_priority,
            artifact.personal_linkedin_count + artifact.personal_email_count,
            artifact.profile_link_count,
            artifact.url,
        )

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
            "closed": False,
            "tasks": set(),
            "handler": None,
        }

        async def _handle_response(response):
            # Time cap
            if state["closed"] or time.time() - state["start_time"] > XHR_TIMEOUT:
                return
            try:
                url = response.url
                parsed = urllib.parse.urlparse(url)
                resp_domain = _normalize_domain(parsed.netloc)

                # Only same-origin requests
                if not _same_site_family(resp_domain, base_domain):
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

        def _consume_task_result(task):
            state["tasks"].discard(task)
            try:
                task.exception()
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        def on_response(response):
            if state["closed"]:
                return
            task = asyncio.create_task(_handle_response(response))
            state["tasks"].add(task)
            task.add_done_callback(_consume_task_result)

        state["handler"] = on_response
        page.on("response", on_response)
        return state

    async def _teardown_xhr_interception(self, page, xhr_state: Optional[Dict]):
        if not xhr_state:
            return
        xhr_state["closed"] = True
        handler = xhr_state.get("handler")
        if page and handler:
            try:
                page.remove_listener("response", handler)
            except Exception:
                pass
        tasks = list(xhr_state.get("tasks") or [])
        if not tasks:
            return
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ─── Static fetch (extraction only) ───────────────────────────────────────

    async def _fetch_static_with_artifact(
        self, session: aiohttp.ClientSession, url: str
    ) -> Tuple[Set[str], Set[str], Optional[PageArtifact]]:
        """Fetch a page statically. Returns (linkedin_urls, emails, page_artifact)."""
        if await self.pacer.should_skip(url):
            return set(), set(), None
        await self.pacer.pace(url)
        try:
            async with session.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=10),
                ssl=self._ssl_ctx
            ) as response:
                status = response.status
                text = await response.text()
                if _is_block_page(text, status):
                    logger.warning(f"Block page detected (static) for {url} [HTTP {status}]")
                    await self.pacer.record_failure(url, is_block=True)
                    return set(), set(), None
                if status != 200:
                    await self.pacer.record_failure(url)
                    return set(), set(), None
                soup = BeautifulSoup(text, 'html.parser')
                final_url = str(response.url)
                found = self._extract_from_soup(soup, final_url)
                found.update(self._extract_from_text(text))
                emails = self._extract_emails_from_text(text)
                artifact = self._make_page_artifact(final_url, soup, found, emails)
                await self.pacer.record_success(url)
                return found, emails, artifact
        except Exception as e:
            logger.warning(f"Static fetch failed for {url}: {e}")
            await self.pacer.record_failure(url)
            return set(), set(), None

    async def _fetch_static(self, session: aiohttp.ClientSession, url: str) -> Tuple[Set[str], Set[str]]:
        """Fetch a page statically. Returns (linkedin_urls, emails)."""
        found, emails, _ = await self._fetch_static_with_artifact(session, url)
        return found, emails

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
            try:
                snapshot = await page.evaluate("""() => ({
                    anchors: document.querySelectorAll('a[href]').length,
                    textLen: (document.body && document.body.innerText || '').length,
                    elements: document.querySelectorAll('*').length,
                    hasTeamContent: !!(
                        document.querySelector('.team, [class*="people"], [class*="medewerker"], [class*="team"], [class*="staff"], [class*="employee"]') ||
                        document.querySelector('[id*="team"], [id*="people"], [id*="medewerker"]')
                    )
                })""")
            except Exception:
                await asyncio.sleep(poll_interval)
                continue

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
        _empty = {"linkedin": set(), "internal_links": [], "emails": set(),
                  "framework_routes": [], "xhr_paths": [], "soup": None,
                  "final_url": url, "timed_out": False, "blocked": False}
        if await self.pacer.should_skip(url):
            return {**_empty, "blocked": True}
        await self.pacer.pace(url)

        found = set()
        page = None
        context = None
        xhr_paths = []
        framework_routes = []
        internal_links_ordered = []
        emails = set()
        soup = None
        final_url = url
        timed_out = False
        xhr_state = None
        try:
            context = await self.browser.new_context(user_agent=USER_AGENT)
            page = await context.new_page()

            # Block static assets
            await page.route("**/*.{png,jpg,jpeg,gif,css,woff,woff2}", lambda route: route.abort())

            # Block trackers/analytics
            for pattern in BLOCKED_TRACKER_PATTERNS:
                await page.route(pattern, lambda route: route.abort())

            # Set up XHR/response interception
            base_domain = _normalize_domain(urllib.parse.urlparse(url).netloc)
            xhr_state = self._setup_xhr_interception(page, base_domain)

            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_SECONDS * 1000)
                await self._wait_until_ready(page)
                # Check for block page via HTTP status
                if resp and resp.status in (403, 429, 503):
                    body = await page.content() if page else ""
                    if _is_block_page(body, resp.status):
                        logger.warning(f"Block page detected (Playwright) for {url} [HTTP {resp.status}]")
                        await self.pacer.record_failure(url, is_block=True)
                        return {**_empty, "blocked": True, "timed_out": False}
            except PlaywrightTimeoutError:
                timed_out = True
                logger.warning(f"Timeout loading {url} in Playwright, proceeding with what loaded.")

            final_url = page.url or url
            effective_domain = _normalize_domain(urllib.parse.urlparse(final_url).netloc) or base_domain

            # Extract framework globals before doing anything else
            try:
                framework_routes = await self._extract_framework_routes(page)
            except Exception as e:
                logger.warning(f"Framework route extraction failed for {url}: {e}")
                framework_routes = []

            # Collect XHR-discovered paths
            xhr_paths = list(xhr_state["paths"])
            if xhr_paths:
                logger.info(f"XHR interception: discovered {len(xhr_paths)} paths from {url}")

            # 1) Extract anchors with text
            try:
                anchors = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                        href: a.href,
                        text: (a.innerText || a.textContent || '').trim().toLowerCase(),
                        aria: (a.getAttribute('aria-label') || '').trim().toLowerCase(),
                        title: (a.getAttribute('title') || '').trim().toLowerCase()
                    }));
                }""")
            except Exception as e:
                logger.warning(f"Anchor extraction failed for {url}: {e}")
                anchors = []

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
                    href_domain = _normalize_domain(parsed.netloc)
                    is_internal = (not parsed.netloc) or _same_site_family(href_domain, effective_domain)
                    if not is_internal:
                        continue

                    full_int_url = urllib.parse.urljoin(final_url, href)

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
            try:
                content = await page.content()
            except Exception as e:
                logger.warning(f"Content extraction failed for {url}: {e}")
                content = ""

            if content:
                found.update(self._extract_from_text(content))

                # 2b) Extract emails
                emails = self._extract_emails_from_text(content)

                # 3) Internal links from raw HTML regex
                for domain in {base_domain, effective_domain}:
                    if not domain:
                        continue
                    re_internal = re.compile(
                        r'(https?:\/\/(?:www\.)?' + re.escape(domain) + r'\/[a-zA-Z0-9%\-_.\/]+)',
                        re.I
                    )
                    for m in re_internal.findall(content):
                        m = m.strip('"\'')
                        if any(ext in m.lower() for ext in ['.js', '.css', '.png', '.jpg', '.jpeg', '.svg', '.woff', '.woff2', '.gif', '.pdf', '.ttf', '.eot']):
                            continue
                        internal_links_ordered.append(m)

            internal_links = dedupe_keep_order(internal_links_ordered)
            if content:
                # Check rendered content for block page signatures
                if _is_block_page(content, 200):
                    logger.warning(f"Block page detected in rendered content for {url}")
                    await self.pacer.record_failure(url, is_block=True)
                    return {**_empty, "blocked": True, "timed_out": timed_out}
                soup = BeautifulSoup(content, 'html.parser')

            await self.pacer.record_success(url)
            return {
                "linkedin": found,
                "internal_links": internal_links,
                "emails": emails,
                "framework_routes": framework_routes,
                "xhr_paths": xhr_paths,
                "soup": soup,
                "final_url": final_url,
                "timed_out": timed_out,
                "blocked": False,
            }

        except Exception as e:
            logger.error(f"Playwright error for {url}: {e}")
            await self.pacer.record_failure(url)
            return {
                "linkedin": found,
                "internal_links": [],
                "emails": set(),
                "framework_routes": [],
                "xhr_paths": [],
                "soup": None,
                "final_url": final_url,
                "timed_out": timed_out,
                "blocked": False,
            }
        finally:
            await self._teardown_xhr_interception(page, xhr_state)
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

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

        # ── Phase 1: Homepage pass (Playwright + static + sitemap in parallel) ──

        async def _homepage_playwright(target_url: str):
            return await self._process_playwright(target_url)

        async def _homepage_static(target_url: str):
            try:
                async with aiohttp.ClientSession() as tmp_session:
                    async with tmp_session.get(
                        target_url,
                        headers={"User-Agent": USER_AGENT},
                        timeout=aiohttp.ClientTimeout(total=15),
                        ssl=self._ssl_ctx
                    ) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            soup = BeautifulSoup(text, 'html.parser')
                            li = self._extract_from_soup(soup, str(resp.url))
                            li.update(self._extract_from_text(text))
                            emails = self._extract_emails_from_text(text)
                            artifact = self._make_page_artifact(str(resp.url), soup, li, emails)
                            return li, emails, soup, artifact
            except Exception:
                pass
            return set(), set(), None, None

        async def _homepage_sitemap(target_url: str):
            try:
                async with aiohttp.ClientSession() as tmp_session:
                    return await self._fetch_sitemap(tmp_session, target_url)
            except Exception:
                return []

        async def _run_homepage_bundle(target_url: str):
            logger.info(f"Processing {target_url} — homepage pass (Playwright + static + sitemap)...")
            return await asyncio.gather(
                _homepage_playwright(target_url),
                _homepage_static(target_url),
                _homepage_sitemap(target_url)
            )

        homepage_targets = [start_url]
        if start_url.startswith("http://"):
            https_start_url = "https://" + start_url[len("http://"):]
            homepage_targets = [https_start_url, start_url]

        selected_start_url = start_url
        pw_result = {"linkedin": set(), "emails": set(), "internal_links": [], "framework_routes": [], "xhr_paths": [], "soup": None, "final_url": start_url}
        static_result = (set(), set(), None, None)
        sitemap_urls = []

        for idx, target_url in enumerate(homepage_targets):
            bundle_pw_result, bundle_static_result, bundle_sitemap_urls = await _run_homepage_bundle(target_url)
            bundle_static_linkedin, bundle_static_emails, bundle_homepage_soup, bundle_homepage_artifact = bundle_static_result
            bundle_viable = (
                not _playwright_result_is_sparse(bundle_pw_result)
                or bundle_homepage_artifact is not None
                or bool(bundle_sitemap_urls)
            )
            pw_result = bundle_pw_result
            static_result = bundle_static_result
            sitemap_urls = bundle_sitemap_urls
            if bundle_viable or idx == len(homepage_targets) - 1:
                selected_start_url = target_url
                static_linkedin = bundle_static_linkedin
                static_emails = bundle_static_emails
                homepage_soup = bundle_homepage_soup
                homepage_static_artifact = bundle_homepage_artifact
                break

        start_url = selected_start_url

        pw_home_url = pw_result.get("final_url") or start_url
        static_home_url = homepage_static_artifact.url if homepage_static_artifact else start_url

        adopted_home_url = start_url
        start_domain = _normalize_domain(urllib.parse.urlparse(start_url).netloc)
        for candidate_url in (pw_home_url, static_home_url):
            candidate_domain = _normalize_domain(urllib.parse.urlparse(candidate_url).netloc)
            if candidate_url and candidate_domain and _same_brand_homepage_redirect(candidate_domain, start_domain):
                adopted_home_url = candidate_url
                break

        parsed = urllib.parse.urlparse(adopted_home_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # Build candidate pool after homepage pass so redirects can redefine the crawl base.
        pool = CandidatePool(adopted_home_url)

        # Unpack Playwright results
        all_found.update(pw_result["linkedin"])
        all_emails.update(pw_result["emails"])
        discovered_internal_links = pw_result["internal_links"]
        framework_routes = pw_result["framework_routes"]
        xhr_paths = pw_result["xhr_paths"]
        self._merge_page_artifact(
            page_artifacts,
            self._make_page_artifact(pw_home_url, pw_result.get("soup"), pw_result["linkedin"], pw_result["emails"])
        )

        # Unpack static results
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

        # ── Score, rank, and truncate (reduce if domain is fragile) ──
        reduce_expansion = await self.pacer.should_reduce_expansion(start_url)
        effective_subpage_limit = SUBPAGE_LIMIT // 2 if reduce_expansion else SUBPAGE_LIMIT
        effective_l2_limit = LEVEL2_LIMIT // 2 if reduce_expansion else LEVEL2_LIMIT
        if reduce_expansion:
            logger.info(f"  Reduced expansion for {start_url}: subpage={effective_subpage_limit}, L2={effective_l2_limit}")
        final_list = pool.ranked(effective_subpage_limit)

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

        async def _process_high_value(session, page_url, allow_level2=True):
            """Full treatment for a high-value page: Playwright + static + level-2."""
            async with pw_semaphore:
                logger.info(f"  -> Full check (Playwright + static): {page_url}")

                pw_res, static_res = await asyncio.gather(
                    self._process_playwright(page_url),
                    self._fetch_static_with_artifact(session, page_url)
                )

                if _is_people_page_url(page_url) and _playwright_result_is_sparse(pw_res):
                    logger.info(f"    -> Retrying sparse Playwright result for people page: {page_url}")
                    retry_res = await self._process_playwright(page_url)
                    if not _playwright_result_is_sparse(retry_res):
                        pw_res = retry_res

                new_links = pw_res["internal_links"]
                st_li, st_em, st_artifact = static_res

                artifact_linkedin = pw_res["linkedin"] | st_li
                artifact_emails = pw_res["emails"] | st_em
                page_linkedin = set(artifact_linkedin)
                page_emails = set(artifact_emails)

                # ── Page classification: gate level-2 expansion ──
                page_soup = pw_res.get("soup")
                page_classification = None
                suppress_l2 = False
                hard_auth_page = False
                if page_soup:
                    page_classification = classify_page(page_soup, page_url)
                    hard_auth_page = is_hard_auth_page(page_url, page_soup, page_classification)
                    suppress_l2 = should_suppress_level2(page_classification, page_soup, page_url)
                    if suppress_l2:
                        logger.info(f"    -> Suppressing level-2 for {page_url} "
                                    f"(signals: {page_classification.get('signals', [])})")

                # Level-2 crawl: check new internal links from this page
                # Skip if classifier says auth/article dominates people
                if allow_level2 and not suppress_l2:
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
                        level2_links = level2_links[:effective_l2_limit]
                        already_checked.update(level2_links)

                    if level2_links:
                        logger.info(f"    -> {len(level2_links)} level-2 links from {page_url}")
                        l2_tasks = [_bounded_static(session, l2) for l2 in level2_links]
                        l2_results = await asyncio.gather(*l2_tasks)
                        for l2_li, l2_em in l2_results:
                            page_linkedin.update(l2_li)
                            page_emails.update(l2_em)

                artifact_soup = None if hard_auth_page else page_soup
                if artifact_soup is not None and st_artifact and st_artifact.soup:
                    pw_artifact = self._make_page_artifact(page_url, artifact_soup, pw_res["linkedin"], pw_res["emails"])
                    if self._artifact_soup_quality_key(st_artifact) > self._artifact_soup_quality_key(pw_artifact):
                        artifact_soup = st_artifact.soup
                elif artifact_soup is None and st_artifact and st_artifact.soup and not hard_auth_page:
                    artifact_soup = st_artifact.soup

                artifact = self._make_page_artifact(page_url, artifact_soup, artifact_linkedin, artifact_emails)
                if artifact and page_classification:
                    artifact.classification = page_classification
                return page_linkedin, page_emails, artifact, bool(pw_res.get("timed_out"))

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

            recovery_mode = bool(pw_result.get("timed_out"))
            site_playwright_timeouts = 1 if pw_result.get("timed_out") else 0
            remaining_high_value_urls = list(high_value_urls)
            hv_results = []

            while remaining_high_value_urls:
                if recovery_mode:
                    narrowed = _recovery_filter_urls(remaining_high_value_urls)
                    if len(narrowed) != len(remaining_high_value_urls):
                        logger.info(
                            f"  Recovery mode active: narrowing remaining high-value pages "
                            f"from {len(remaining_high_value_urls)} to {len(narrowed)} obvious people pages"
                        )
                    remaining_high_value_urls = narrowed
                    if not remaining_high_value_urls:
                        break

                wave = remaining_high_value_urls[:PW_CONCURRENCY]
                remaining_high_value_urls = remaining_high_value_urls[PW_CONCURRENCY:]
                wave_results = await asyncio.gather(
                    *[_process_high_value(session, u, allow_level2=not recovery_mode) for u in wave]
                )
                hv_results.extend(wave_results)

                site_playwright_timeouts += sum(1 for _, _, _, pw_timed_out in wave_results if pw_timed_out)
                if not recovery_mode and site_playwright_timeouts >= RECOVERY_MODE_TIMEOUT_THRESHOLD:
                    recovery_mode = True
                    logger.info(
                        f"  Recovery mode activated for {start_url} after {site_playwright_timeouts} "
                        f"Playwright timeouts; remaining crawl will focus on people pages only"
                    )

            if recovery_mode:
                narrowed_static_urls = _recovery_filter_urls(static_urls)
                if len(narrowed_static_urls) != len(static_urls):
                    logger.info(
                        f"  Recovery mode active: narrowing static-only pages "
                        f"from {len(static_urls)} to {len(narrowed_static_urls)} obvious people pages"
                    )
                static_urls = narrowed_static_urls

            st_results = await asyncio.gather(*[_bounded_static(session, u) for u in static_urls])

            for hv_li, hv_em, hv_artifact, _ in hv_results:
                all_found.update(hv_li)
                all_emails.update(hv_em)
                self._merge_page_artifact(page_artifacts, hv_artifact)

            for st_li, st_em in st_results:
                all_found.update(st_li)
                all_emails.update(st_em)

        ordered_artifacts = sorted(
            list(page_artifacts.values()),
            key=self._artifact_priority_key,
            reverse=True,
        )
        if ordered_artifacts:
            logger.info("Page evidence leaderboard:")
            for artifact in ordered_artifacts[:5]:
                logger.info(
                    "  -> %s | priority=%s li=%s email=%s profile_links=%s",
                    artifact.url,
                    artifact.signal_priority,
                    artifact.personal_linkedin_count,
                    artifact.personal_email_count,
                    artifact.profile_link_count,
                )

        return sorted(list(all_found)), sorted(list(all_emails)), ordered_artifacts


# ─── Orchestration functions (kept from v1) ───────────────────────────────────

def generate_company_id(company_name: str) -> str:
    date_prefix = datetime.now().strftime("%Y%m%d")
    hash_suffix = hashlib.md5(company_name.lower().strip().encode()).hexdigest()[:6]
    return f"{date_prefix}-{hash_suffix}"


class PersonExtractor:
    """Extracts person data (name, role, email, linkedin) using 5 fallback strategies."""

    def __init__(self):
        self._ssl_context = None

    @staticmethod
    def _artifact_priority_key(artifact: PageArtifact) -> Tuple[int, int, int, str]:
        return (
            getattr(artifact, "signal_priority", 0),
            getattr(artifact, "personal_linkedin_count", 0) + getattr(artifact, "personal_email_count", 0),
            getattr(artifact, "profile_link_count", 0),
            artifact.url,
        )

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
        if not t:
            return False
        for kw in role_keywords:
            if t == kw or t.startswith(kw + " ") or t.endswith(" " + kw):
                return True
            if re.search(r'\b' + re.escape(kw) + r'\b', t):
                return True
        return False

    @staticmethod
    def _looks_like_person_name(text: str) -> bool:
        clean = re.sub(r'\s+', ' ', (text or '').strip())
        words = clean.split()
        if len(words) < 2 or len(words) > 5:
            return False
        tokens = set(_normalize_tokens(clean))
        if tokens & PERSON_NAME_BLOCKLIST:
            return False
        # Common lowercase particles in Dutch/German/French/Spanish names
        particles = {'de', 'van', 'den', 'der', 'het', 'ten', 'ter', 'von',
                      'du', 'le', 'la', 'di', 'el', 'al', 'bin', 'op'}
        capitalized = re.compile(r"^[A-Z\u00C0-\u024F][A-Za-z\u00C0-\u024F''\-]+$")
        cap_count = 0
        for word in words:
            if word.lower() in particles:
                continue  # particles are always allowed
            if not capitalized.match(word):
                return False
            cap_count += 1
        return cap_count >= 2  # At least 2 non-particle capitalized words

    @staticmethod
    def _node_text(node) -> str:
        return node.get_text(" ", strip=True) if node else ""

    def _is_noise_text(self, text: str) -> bool:
        t = (text or "").strip().lower()
        if not t:
            return True
        if t in CTA_NOISE_TEXT:
            return True
        if _looks_like_phone_text(text):
            return True
        if _is_legal_or_footer_text(text):
            return True
        if '@' in t:
            return True
        if t.startswith('http'):
            return True
        return False

    def _role_candidate_texts(self, container) -> List[str]:
        seen = set()
        texts: List[str] = []
        for node in container.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'p', 'span', 'strong', 'b', 'div', 'li']):
            text = self._node_text(node)
            normalized = re.sub(r"\s+", " ", text).strip()
            if not normalized or normalized in seen:
                continue
            if len(normalized) > 120:
                continue
            if _looks_like_phone_text(normalized) or _is_legal_or_footer_text(normalized):
                continue
            texts.append(normalized)
            seen.add(normalized)
        return texts

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

    @staticmethod
    def _normalize_mail_href(href: str) -> str:
        if not href:
            return ""
        return re.sub(r'^mail(?:to)?:', '', href, flags=re.I).strip()

    @staticmethod
    def _is_valid_email_candidate(email: str) -> bool:
        if not email:
            return False
        email = email.lower().strip()
        local_part, _, domain = email.partition('@')
        if not local_part or not domain:
            return False
        if any(excl in email for excl in EXCLUDED_EMAILS):
            return False
        if domain in EXCLUDED_EMAIL_DOMAINS or domain.endswith('.wixpress.com'):
            return False
        if RE_MACHINE_EMAIL_LOCAL.fullmatch(local_part):
            return False
        if any(ext in email for ext in ['.png', '.jpg', '.gif', '.svg', '.css', '.js']):
            return False
        return True

    def _find_inline_emails(self, container) -> List[str]:
        if not container:
            return []

        emails: List[str] = []
        seen: Set[str] = set()

        for tag in container.find_all(True):
            if tag.name in {'script', 'style', 'svg', 'path'}:
                continue
            if any(getattr(child, 'name', None) for child in tag.children):
                continue
            text = self._node_text(tag)
            if not text or len(text) > 240:
                continue
            for email in RE_EMAIL.findall(text):
                normalized = email.lower().strip()
                if normalized in seen or not self._is_valid_email_candidate(normalized):
                    continue
                seen.add(normalized)
                emails.append(normalized)

        return emails

    def _find_email(self, container) -> str:
        mail_anchor = container.find('a', href=re.compile(r'^mail(?:to)?:', re.I))
        if mail_anchor:
            email = self._normalize_mail_href(mail_anchor.get('href', ''))
            if self._is_valid_email_candidate(email):
                return email

        inline_emails = self._find_inline_emails(container)
        if not inline_emails:
            return ""

        for email in inline_emails:
            if _is_personal_email(email):
                return email
        return inline_emails[0]

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
            container.find('a', href=re.compile(r'^mail(?:to)?:', re.I)) or
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
        raw_name_text = self._node_text(name_node)
        if not raw_name_text:
            return None

        combined = self._split_combined_name_role(raw_name_text, role_keywords)
        name = combined[0] if combined else raw_name_text

        leaves = self._get_leaf_nodes(container)
        role_text = combined[1] if combined else ""
        for text in self._role_candidate_texts(container):
            if text == raw_name_text or text == name:
                continue
            if self._matches_role(text, role_keywords):
                role_text = text
                break

        bio_leaf = None
        bio_candidates = []
        for leaf in leaves:
            text = self._node_text(leaf)
            if text == raw_name_text or text == name:
                continue
            if len(text) > 40 and not self._matches_role(text, role_keywords):
                if _looks_like_phone_text(text) or _is_legal_or_footer_text(text):
                    continue
                bio_candidates.append(leaf)
        if bio_candidates:
            bio_leaf = max(bio_candidates, key=lambda leaf: len(self._node_text(leaf)))

        return {
            "name": name,
            "role": role_text,
            "linkedin": self._find_personal_linkedin(container),
            "email": self._find_email(container),
            "bio": self._node_text(bio_leaf) or "",
            "confidence": confidence,
            "strategy": strategy,
            "evidence": evidence,
            "source_url": page_url,
        }

    @staticmethod
    def _match_rank_value(rank: str) -> int:
        return {"none": 0, "weak": 1, "medium": 2, "strong": 3}.get(rank, 0)

    def _split_combined_name_role(self, text: str, role_keywords: List[str]) -> Optional[Tuple[str, str]]:
        return _split_combined_name_role_static(text, role_keywords)

    def _container_personal_signals(self, container) -> Tuple[List[str], List[str]]:
        emails = []
        linkedins = []
        for anchor in container.find_all('a', href=True):
            href = (anchor.get('href') or '').strip()
            if href.lower().startswith(('mailto:', 'mail:')):
                email = self._normalize_mail_href(href)
                if _is_personal_email(email) and email not in emails:
                    emails.append(email)
            elif _is_personal_linkedin(href):
                linkedin = self._clean_linkedin_url(href)
                if linkedin and linkedin not in linkedins:
                    linkedins.append(linkedin)
        for email in self._find_inline_emails(container):
            if _is_personal_email(email) and email not in emails:
                emails.append(email)
        return emails, linkedins

    def _container_soft_people_signals(self, container, page_url: str) -> Dict[str, int]:
        profile_links = 0
        any_linkedin = 0
        tel_count = 0
        for anchor in container.find_all('a', href=True):
            href = (anchor.get('href') or '').strip()
            if not href:
                continue
            if 'linkedin.com' in href.lower():
                any_linkedin += 1
            if href.lower().startswith('tel:'):
                tel_count += 1
            if _is_profile_like_href(href, page_url, anchor.get_text(" ", strip=True)):
                profile_links += 1
        return {
            "profile_links": profile_links,
            "any_linkedin": any_linkedin,
            "tel_count": tel_count,
        }

    def _candidate_name_nodes(self, container, role_keywords: List[str]) -> List:
        candidates = []
        seen_ids: Set[int] = set()

        for node in container.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'strong', 'b']):
            text = self._node_text(node)
            if not text or len(text) >= 120:
                continue
            combined = self._split_combined_name_role(text, role_keywords)
            if not combined and (not self._looks_like_person_name(text) or self._matches_role(text, role_keywords)):
                continue
            if id(node) not in seen_ids:
                candidates.append(node)
                seen_ids.add(id(node))

        for leaf in self._get_leaf_nodes(container):
            text = self._node_text(leaf)
            if not text or len(text) >= 120:
                continue
            combined = self._split_combined_name_role(text, role_keywords)
            if not combined and (not self._looks_like_person_name(text) or self._matches_role(text, role_keywords)):
                continue
            if id(leaf) not in seen_ids:
                candidates.append(leaf)
                seen_ids.add(id(leaf))

        return candidates

    def _find_best_signal_name_node(self, container, role_keywords: List[str],
                                    signal_emails: Optional[List[str]] = None,
                                    signal_linkedins: Optional[List[str]] = None) -> Tuple[Optional[object], str]:
        signal_emails = signal_emails or []
        signal_linkedins = signal_linkedins or []
        name_candidates = self._candidate_name_nodes(container, role_keywords)
        if not name_candidates:
            return None, "none"

        best_node = None
        best_rank = "none"
        best_score = 0

        for node in name_candidates:
            raw_text = self._node_text(node)
            combined = self._split_combined_name_role(raw_text, role_keywords)
            text = combined[0] if combined else raw_text
            local_best_rank = "none"
            local_best_score = 0

            for email in signal_emails:
                rank = _match_name_to_signal(text, email=email)
                score = self._match_rank_value(rank)
                if score > local_best_score:
                    local_best_score = score
                    local_best_rank = rank

            for linkedin in signal_linkedins:
                rank = _match_name_to_signal(text, linkedin_url=linkedin)
                score = self._match_rank_value(rank)
                if score > local_best_score:
                    local_best_score = score
                    local_best_rank = rank

            if getattr(node, 'name', '') in {'h1', 'h2', 'h3', 'h4', 'h5'}:
                local_best_score += 0.2

            if local_best_score > best_score:
                best_node = node
                best_score = local_best_score
                best_rank = local_best_rank

        # If there's only one clear name candidate in the container, allow a weak fallback.
        if best_node and best_rank == "weak" and len(name_candidates) == 1:
            return best_node, "medium"

        return best_node, best_rank

    def _build_confirmed_person(self, container, name_node, page_url: str,
                                confidence: str, strategy: str, evidence: str,
                                role_keywords: List[str], signal_email: str = "",
                                signal_linkedin: str = "") -> Optional[Dict]:
        person = self._extract_person_from_container(
            container, name_node, page_url, confidence, strategy, evidence, role_keywords
        )
        if not person:
            return None
        if signal_email:
            person["email"] = signal_email
        if signal_linkedin:
            person["linkedin"] = signal_linkedin
        parent = container.parent
        person["parent_section_id"] = str(id(parent)) if parent else "root"
        person["_container_id"] = id(container)
        person["_container"] = container
        person["_name_node"] = name_node
        return person

    def _collect_soft_ancestor_candidates(self, soup: BeautifulSoup, page_url: str,
                                          role_keywords: List[str],
                                          seen_container_ids: Optional[Set[int]] = None) -> Dict[Tuple[int, int], List[Dict]]:
        """Collect role-backed soft candidates and group them by shared ancestor.

        This is designed for builders that hash classes per element, where
        selector-based consensus fails but cards still share a parent or
        grandparent wrapper.
        """
        seen_container_ids = seen_container_ids or set()
        pending_by_ancestor: Dict[Tuple[int, int], List[Dict]] = {}
        leaves = [leaf for leaf in self._get_leaf_nodes(soup) if len(self._node_text(leaf)) < 120]

        for leaf in leaves:
            if not self._matches_role(self._node_text(leaf), role_keywords):
                continue

            current = leaf
            for _ in range(MAX_CONTAINER_ANCESTOR_WALK):
                if current is None or not getattr(current, 'name', None):
                    break
                cid = id(current)
                if cid in seen_container_ids:
                    break
                if _is_in_global_zone(current):
                    break

                desc_count = self._container_descendants(current)
                if desc_count < MIN_CONTAINER_DESCENDANTS:
                    current = current.parent
                    continue
                if desc_count > MAX_CONTAINER_DESCENDANTS:
                    break

                emails, linkedins = self._container_personal_signals(current)
                if emails or linkedins:
                    break

                soft_signals = self._container_soft_people_signals(current, page_url)
                if soft_signals["profile_links"] == 0 and soft_signals["any_linkedin"] == 0:
                    current = current.parent
                    continue

                name_candidates = self._candidate_name_nodes(current, role_keywords)
                if not name_candidates:
                    current = current.parent
                    continue

                entry = {
                    "container": current,
                    "name_node": name_candidates[0],
                    "shape": _container_shape(current),
                    "container_id": cid,
                    "role_keywords": role_keywords,
                    "soft_signals": soft_signals,
                }

                ancestor = current.parent
                depth = 1
                while ancestor is not None and depth <= 2:
                    if getattr(ancestor, 'name', None) and not _is_in_global_zone(ancestor):
                        pending_by_ancestor.setdefault((id(ancestor), depth), []).append(entry)
                    ancestor = ancestor.parent
                    depth += 1
                break

        return pending_by_ancestor

    def _promote_ancestor_consensus(self, pending_by_ancestor: Dict[Tuple[int, int], List[Dict]],
                                    page_url: str, role_keywords: List[str]) -> List[Dict]:
        promoted = []
        promoted_ids: Set[int] = set()

        for (ancestor_id, depth), entries in pending_by_ancestor.items():
            unique_entries = []
            seen_ids_local = set()
            for entry in entries:
                cid = entry["container_id"]
                if cid in seen_ids_local:
                    continue
                seen_ids_local.add(cid)
                unique_entries.append(entry)

            if len(unique_entries) < 2:
                continue

            shape_groups: List[List[Dict]] = []
            for entry in unique_entries:
                placed = False
                for group in shape_groups:
                    if _shapes_match(entry["shape"], group[0]["shape"]):
                        group.append(entry)
                        placed = True
                        break
                if not placed:
                    shape_groups.append([entry])

            for group in shape_groups:
                if len(group) < 2:
                    continue
                for entry in group:
                    cid = entry["container_id"]
                    if cid in promoted_ids:
                        continue
                    container = entry["container"]
                    name_node = entry["name_node"]
                    raw_name_text = self._node_text(name_node)
                    combined = self._split_combined_name_role(raw_name_text, role_keywords)
                    name_text = combined[0] if combined else raw_name_text
                    if not name_text or not self._looks_like_person_name(name_text):
                        continue

                    role_text = combined[1] if combined else ""
                    for leaf in self._get_leaf_nodes(container):
                        text = self._node_text(leaf)
                        if text == raw_name_text or text == name_text or len(text) >= 120:
                            continue
                        if self._matches_role(text, role_keywords):
                            role_text = text
                            break

                    parent = container.parent
                    person = {
                        "name": name_text,
                        "role": role_text,
                        "linkedin": self._find_personal_linkedin(container),
                        "email": self._find_email(container),
                        "bio": "",
                        "confidence": "secondary",
                        "strategy": "ancestor_consensus",
                        "evidence": f"ancestor_consensus_depth{depth}",
                        "source_url": page_url,
                        "parent_section_id": str(id(parent)) if parent else "root",
                        "_container_id": cid,
                        "_container": container,
                        "_name_node": name_node,
                    }
                    promoted.append(person)
                    promoted_ids.add(cid)

        return promoted

    def _extract_role_first_confirmed(self, soup: BeautifulSoup, page_url: str,
                                      role_keywords: List[str]) -> List[Dict]:
        persons = []
        seen_containers: Set[int] = set()
        leaves = [leaf for leaf in self._get_leaf_nodes(soup) if len(self._node_text(leaf)) < 120]

        for leaf in leaves:
            if not self._matches_role(self._node_text(leaf), role_keywords):
                continue

            current = leaf
            for _ in range(MAX_CONTAINER_ANCESTOR_WALK):
                if current is None or not getattr(current, 'name', None):
                    break
                cid = id(current)
                if cid in seen_containers:
                    break
                if _is_in_global_zone(current):
                    break

                desc_count = self._container_descendants(current)
                if desc_count < MIN_CONTAINER_DESCENDANTS:
                    current = current.parent
                    continue
                if desc_count > MAX_CONTAINER_DESCENDANTS:
                    break

                # Section-wrapper guard: a container with 3+ role-keyword
                # leaves is a section wrapper, not an individual person card.
                role_leaf_count = sum(
                    1 for l in self._get_leaf_nodes(current)
                    if self._matches_role(self._node_text(l), role_keywords)
                    and len(self._node_text(l)) < 120
                )
                if role_leaf_count > 2:
                    current = current.parent
                    continue

                emails, linkedins = self._container_personal_signals(current)
                if not emails and not linkedins:
                    current = current.parent
                    continue

                name_node, match_rank = self._find_best_signal_name_node(
                    current, role_keywords, emails, linkedins
                )
                if name_node and match_rank in {"strong", "medium"}:
                    person = self._build_confirmed_person(
                        current,
                        name_node,
                        page_url,
                        "primary" if match_rank == "strong" else "secondary",
                        "role_signal_template",
                        f"role_then_signal_{match_rank}",
                        role_keywords,
                    )
                    if person:
                        persons.append(person)
                        seen_containers.add(cid)
                    break

                current = current.parent

        return persons

    def _extract_signal_first_confirmed(self, soup: BeautifulSoup, page_url: str,
                                        role_keywords: List[str],
                                        seen_container_ids: Optional[Set[int]] = None) -> List[Dict]:
        persons = []
        seen_container_ids = seen_container_ids or set()
        seen_signals: Set[str] = set()

        for anchor in soup.find_all('a', href=True):
            href = (anchor.get('href') or '').strip()
            if not href or _is_in_global_zone(anchor):
                continue

            signal_email = ""
            signal_linkedin = ""
            if href.lower().startswith(('mailto:', 'mail:')):
                email = self._normalize_mail_href(href)
                if not _is_personal_email(email):
                    continue
                signal_email = email
            elif _is_personal_linkedin(href):
                signal_linkedin = self._clean_linkedin_url(href)
            else:
                continue

            signal_key = signal_email or signal_linkedin
            if not signal_key or signal_key in seen_signals:
                continue
            seen_signals.add(signal_key)

            current = anchor.parent
            for _ in range(MAX_CONTAINER_ANCESTOR_WALK):
                if current is None or not getattr(current, 'name', None):
                    break
                cid = id(current)
                if cid in seen_container_ids:
                    break
                if _is_in_global_zone(current):
                    break

                desc_count = self._container_descendants(current)
                if desc_count < MIN_CONTAINER_DESCENDANTS:
                    current = current.parent
                    continue
                if desc_count > MAX_CONTAINER_DESCENDANTS:
                    break

                name_node, match_rank = self._find_best_signal_name_node(
                    current, role_keywords,
                    [signal_email] if signal_email else [],
                    [signal_linkedin] if signal_linkedin else [],
                )
                if not name_node or match_rank not in {"strong", "medium"}:
                    current = current.parent
                    continue

                person = self._build_confirmed_person(
                    current,
                    name_node,
                    page_url,
                    "primary" if match_rank == "strong" else "secondary",
                    "signal_template",
                    f"signal_template_{match_rank}",
                    role_keywords,
                    signal_email=signal_email,
                    signal_linkedin=signal_linkedin,
                )
                if person:
                    persons.append(person)
                    seen_container_ids.add(cid)
                break

        return persons

    def _promote_contactless_repeated_grids(self, soup: BeautifulSoup, page_url: str,
                                            role_keywords: List[str],
                                            seen_container_ids: Optional[Set[int]] = None) -> List[Dict]:
        """Promote highly uniform, repeated people grids even when cards have
        no direct contact/profile signals.

        This is intended for sections like contactless team pages:
        repeated image cards, strong person-name density, highly uniform shape.
        """
        seen_container_ids = seen_container_ids or set()
        promoted: List[Dict] = []

        for parent in soup.find_all(True):
            if _is_in_global_zone(parent):
                continue

            children = [child for child in parent.children if getattr(child, 'name', None)]
            if len(children) < 3:
                continue

            shape_groups: Dict[tuple, List] = {}
            for child in children:
                cid = id(child)
                if cid in seen_container_ids:
                    continue
                if _is_in_global_zone(child):
                    continue
                desc_count = self._container_descendants(child)
                if desc_count < MIN_CONTAINER_DESCENDANTS or desc_count > MAX_CONTAINER_DESCENDANTS:
                    continue
                shape_groups.setdefault(_container_shape(child), []).append(child)

            for shape, group in shape_groups.items():
                if len(group) < 3:
                    continue
                if len(group) / max(len(children), 1) < 0.75:
                    continue

                card_entries = []
                image_count = 0
                role_count = 0

                for child in group:
                    name_candidates = self._candidate_name_nodes(child, role_keywords)
                    if not name_candidates:
                        continue
                    name_node = name_candidates[0]
                    raw_name_text = self._node_text(name_node)
                    combined = self._split_combined_name_role(raw_name_text, role_keywords)
                    clean_name = combined[0] if combined else raw_name_text
                    if not clean_name or not self._looks_like_person_name(clean_name):
                        continue

                    role_text = combined[1] if combined else ""
                    if not role_text:
                        for text in self._role_candidate_texts(child):
                            if text == raw_name_text or text == clean_name:
                                continue
                            if self._matches_role(text, role_keywords):
                                role_text = text
                                break

                    has_img = bool(child.find('img'))
                    if has_img:
                        image_count += 1
                    if role_text:
                        role_count += 1

                    card_entries.append({
                        "container": child,
                        "name_node": name_node,
                        "name": clean_name,
                        "role": role_text,
                    })

                if len(card_entries) < 3:
                    continue
                if len(card_entries) / len(group) < 0.75:
                    continue
                if image_count / len(group) < 0.75:
                    continue
                if role_count < 1:
                    continue

                for entry in card_entries:
                    cid = id(entry["container"])
                    if cid in seen_container_ids:
                        continue
                    person = {
                        "name": entry["name"],
                        "role": entry["role"],
                        "linkedin": self._find_personal_linkedin(entry["container"]),
                        "email": self._find_email(entry["container"]),
                        "bio": "",
                        "confidence": "secondary",
                        "strategy": "contactless_grid",
                        "evidence": "uniform_repeated_people_grid",
                        "source_url": page_url,
                        "parent_section_id": str(id(parent)),
                        "_container_id": cid,
                        "_container": entry["container"],
                        "_name_node": entry["name_node"],
                    }
                    promoted.append(person)
                    seen_container_ids.add(cid)

        return promoted

    # ─── V2 extraction: unified pipeline with all upgrade strategies ─────

    def extract_from_page_artifact_v2(self, soup: BeautifulSoup, page_url: str) -> List[Dict]:
        """Simplified 2-layer extraction pipeline:
        1. Role-first: role hit -> signal-backed card confirmation -> template reuse
        2. Signal-first fallback: email/linkedin anchor -> matched card -> template reuse
        """
        if not soup:
            return []

        role_keywords = self._role_keywords_for_soup(soup)
        role_confirmed = self._extract_role_first_confirmed(soup, page_url, role_keywords)

        seen_container_ids = {
            p.get("_container_id", 0) for p in role_confirmed if p.get("_container_id")
        }
        signal_confirmed = self._extract_signal_first_confirmed(
            soup, page_url, role_keywords, seen_container_ids
        )

        seen_container_ids.update(
            p.get("_container_id", 0) for p in signal_confirmed if p.get("_container_id")
        )
        ancestor_candidates = self._collect_soft_ancestor_candidates(
            soup, page_url, role_keywords, seen_container_ids
        )
        ancestor_promoted = self._promote_ancestor_consensus(
            ancestor_candidates, page_url, role_keywords
        )
        seen_container_ids.update(
            p.get("_container_id", 0) for p in ancestor_promoted if p.get("_container_id")
        )

        contactless_promoted = self._promote_contactless_repeated_grids(
            soup, page_url, role_keywords, seen_container_ids
        )

        all_confirmed = role_confirmed + signal_confirmed + ancestor_promoted + contactless_promoted
        swept = self._sibling_sweep(soup, all_confirmed, page_url, role_keywords)
        return self._dedupe_persons(all_confirmed + swept)

    def _sibling_sweep(self, soup: BeautifulSoup, confirmed_persons: List[Dict],
                        page_url: str, role_keywords: List[str]) -> List[Dict]:
        """After confirmed and promoted cards are identified, sweep sibling
        containers with matching structural fingerprint to find peers that
        lack hard personal signals.

        Uses positional corroboration: the sibling must have a name-like text
        at the same DOM position as the confirmed card's name node.
        """
        if not confirmed_persons:
            return []

        swept = []
        seen_container_ids: Set[int] = set()
        for p in confirmed_persons:
            seen_container_ids.add(p.get("_container_id", 0))

        # Group confirmed cards by parent section
        by_parent: Dict[str, List[Dict]] = {}
        for p in confirmed_persons:
            pid = p.get("parent_section_id", "unknown")
            by_parent.setdefault(pid, []).append(p)

        for parent_id, parent_persons in by_parent.items():
            # Get a reference card to build the fingerprint and name-node path
            ref_person = parent_persons[0]
            ref_container = ref_person.get("_container")
            ref_name_node = ref_person.get("_name_node")
            if not ref_container or not ref_name_node:
                continue

            ref_shape = _container_shape(ref_container)
            ref_name_path = _name_node_path(ref_container, ref_name_node)
            if not ref_name_path:
                continue

            # Get the actual parent element
            parent_element = ref_container.parent
            if not parent_element:
                continue

            # Skip if parent is a global zone
            if _is_in_global_zone(parent_element):
                continue

            confirmed_shapes = []
            confirmed_paths = []
            for person in parent_persons:
                container = person.get("_container")
                name_node = person.get("_name_node")
                if not container or not name_node:
                    continue
                confirmed_shapes.append(_container_shape(container))
                confirmed_paths.append(_name_node_path(container, name_node))

            if not confirmed_shapes:
                continue

            candidate_peers = []
            candidate_peer_ids: Set[int] = set()

            def add_candidate(node):
                if not getattr(node, 'name', None):
                    return
                nid = id(node)
                if nid in candidate_peer_ids:
                    return
                candidate_peer_ids.add(nid)
                candidate_peers.append(node)

            for sibling in parent_element.children:
                add_candidate(sibling)

            if len(parent_persons) >= 2:
                grandparent = parent_element.parent
                parent_shape = _container_shape(parent_element)
                if grandparent and not _is_in_global_zone(grandparent):
                    for row_like in grandparent.children:
                        if not getattr(row_like, 'name', None):
                            continue
                        if id(row_like) == id(parent_element):
                            continue
                        if not _shapes_match(parent_shape, _container_shape(row_like)):
                            continue
                        for cousin in row_like.children:
                            add_candidate(cousin)

            # Sweep siblings / trusted cousins
            for sibling in candidate_peers:
                if not getattr(sibling, 'name', None):
                    continue
                sid = id(sibling)
                if sid in seen_container_ids:
                    continue

                # Check structural match
                sibling_shape = _container_shape(sibling)
                if not any(_shapes_match(shape, sibling_shape) for shape in confirmed_shapes):
                    continue

                # Positional corroboration: find name at same position
                name_node = _find_name_at_position(sibling, ref_name_path, role_keywords)
                best_similarity = 1.0 if name_node else 0.0
                if not name_node:
                    candidate_nodes = self._candidate_name_nodes(sibling, role_keywords)
                    best_node = None
                    for candidate in candidate_nodes:
                        candidate_path = _name_node_path(sibling, candidate)
                        similarity = max(
                            (_path_prefix_similarity(candidate_path, path) for path in confirmed_paths if path),
                            default=0.0,
                        )
                        if similarity > best_similarity:
                            best_similarity = similarity
                            best_node = candidate
                    if best_node and best_similarity >= 0.5:
                        name_node = best_node
                if not name_node:
                    continue

                raw_name_text = name_node.get_text(" ", strip=True)
                combined = self._split_combined_name_role(raw_name_text, role_keywords)
                name_text = combined[0] if combined else raw_name_text
                if not name_text or not _looks_like_person_name_static(name_text):
                    continue

                role_text = combined[1] if combined else ""
                for leaf in self._get_leaf_nodes(sibling):
                    text = self._node_text(leaf)
                    if text == raw_name_text or text == name_text or len(text) >= 120:
                        continue
                    if self._matches_role(text, role_keywords):
                        role_text = text
                        break

                person = {
                    "name": name_text,
                    "role": role_text,
                    "linkedin": self._find_personal_linkedin(sibling),
                    "email": self._find_email(sibling),
                    "bio": "",
                    "confidence": "secondary",
                    "strategy": "sibling_sweep",
                    "evidence": "sibling_sweep",
                    "source_url": page_url,
                    "parent_section_id": parent_id,
                    "_container_id": sid,
                    "_container": sibling,
                    "_name_node": name_node,
                }
                if not person["linkedin"] and not person["email"] and best_similarity < 0.75:
                    continue
                # Extract phone if present
                tel_anchor = sibling.find('a', href=re.compile(r'^tel:', re.I))
                if tel_anchor:
                    person["phone"] = tel_anchor.get('href', '').replace('tel:', '').strip()
                swept.append(person)
                seen_container_ids.add(sid)

        return swept

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
        v2_count = 0
        jsonld_count = 0

        prioritized_artifacts = sorted(
            page_artifacts,
            key=self._artifact_priority_key,
            reverse=True,
        )

        for artifact in prioritized_artifacts:
            if not artifact.soup:
                continue
            # Use the v2 unified pipeline (container + contact-anchor +
            # peer promotion + sibling sweep) instead of the legacy v1 path.
            extracted = self.extract_from_page_artifact_v2(artifact.soup, artifact.url)
            v2_count += len(extracted)
            all_persons.extend(extracted)

            jsonld_people = self.strategy2_json_ld(artifact.soup)
            jsonld_count += len(jsonld_people)
            all_persons.extend(jsonld_people)

        logger.info(f"  Strategy 1 (V2 extraction pipeline): {v2_count} persons")
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

        # Strip internal pipeline fields (BS4 objects, container IDs) that
        # would bloat/break downstream JSON serialisation.
        _internal_keys = {"_container", "_name_node", "_container_id", "parent_section_id"}
        results = list(final_by_name.values())
        for record in results:
            for key in _internal_keys:
                record.pop(key, None)
        return results


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


def _checkpoint_key(site_info: dict) -> str:
    tab = (site_info.get("tab") or "").strip().lower()
    row = site_info.get("row")
    if tab and row not in (None, ""):
        return f"{tab}:{row}"
    url = site_info.get("url") or site_info.get("website") or site_info.get("input_website") or ""
    return url.rstrip("/")


def _load_checkpoint_state(path: str) -> Dict[str, Dict]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            raw = json.load(f)
    except Exception:
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, list):
        return {}
    state = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        key = entry.get("checkpoint_key")
        if not key:
            tab = (entry.get("tab") or "").strip().lower()
            row = entry.get("row")
            if tab and row not in (None, ""):
                key = f"{tab}:{row}"
            else:
                key = (entry.get("website") or "").rstrip("/")
        if key:
            state[key] = entry
    return state


def _save_checkpoint_state(path: str, state: Dict[str, Dict]):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2, default=str)
    os.replace(tmp_path, path)


def _checkpoint_resume_action(site_info: dict, checkpoint_state: Dict[str, Dict]) -> str:
    entry = checkpoint_state.get(_checkpoint_key(site_info))
    if not entry:
        return "process"
    if entry.get("write") == "ok":
        return "mark_only"
    return "process"


async def process_single_site(google_session: aiohttp.ClientSession,
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

        finder = LinkedInFinder()
        try:
            await finder.start()

            async def _run_site():
                logger.info(f"Starting: {url}")
                links, emails, page_artifacts = await finder.find_linkedin_links(url)
                logger.info(f"Done {url} -> {len(links)} LinkedIn, {len(emails)} emails")
                pacer_stats = finder.pacer.get_stats()
                blocked = bool(pacer_stats.get("blocked_domains"))

                persons = []
                if person_extractor and (links or emails or page_artifacts):
                    async with aiohttp.ClientSession() as site_session:
                        persons = await person_extractor.extract_persons(
                            site_session, page_artifacts, links, emails,
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
                    "blocked": blocked,
                }

                if blocked and not (links or emails or page_artifacts or persons):
                    result["error"] = "blocked by site firewall"

                if tab and row:
                    result["tab"] = tab
                    result["row"] = row

                if not tab:
                    await upload_to_google(google_session, url, links, emails, tab, row)

                return result

            return await asyncio.wait_for(_run_site(), timeout=SITE_PROCESS_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error(f"Failed {url}: timed out after {SITE_PROCESS_TIMEOUT}s")
            pacer_stats = finder.pacer.get_stats()
            blocked = bool(pacer_stats.get("blocked_domains"))
            result = {
                "input_website": url,
                "name": name if not isinstance(site_info, str) else "",
                "linkedin_urls": [],
                "company_linkedin": "",
                "emails": [],
                "persons": [],
                "error": f"timed out after {SITE_PROCESS_TIMEOUT}s",
                "blocked": blocked,
            }
            if tab and row:
                result["tab"] = tab
                result["row"] = row
            return result
        except Exception as e:
            logger.error(f"Failed {url}: {e}")
            pacer_stats = finder.pacer.get_stats()
            blocked = bool(pacer_stats.get("blocked_domains"))
            result = {
                "input_website": url,
                "name": name if not isinstance(site_info, str) else "",
                "linkedin_urls": [],
                "company_linkedin": "",
                "emails": [],
                "persons": [],
                "error": str(e),
                "blocked": blocked,
            }
            if tab and row:
                result["tab"] = tab
                result["row"] = row
            return result
        finally:
            await finder.stop()


async def fetch_urls_from_sheet(session: aiohttp.ClientSession) -> List[dict]:
    return await GoogleClient.fetch_urls(session)


# ─── Run lock ─────────────────────────────────────────────────────────────────

class RunLock:
    """File-based run lock to prevent concurrent batch runs."""

    def __init__(self, lock_path: str, run_id: str):
        self._path = lock_path
        self._run_id = run_id
        self._fd = None

    def acquire(self) -> bool:
        try:
            self._fd = open(self._path, "a+")
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd.seek(0)
            self._fd.truncate()
            self._fd.write(json.dumps({
                "pid": os.getpid(),
                "run_id": self._run_id,
                "started": datetime.now().isoformat(),
            }))
            self._fd.flush()
            return True
        except (IOError, OSError):
            if self._fd:
                self._fd.close()
                self._fd = None
            return False

    def release(self):
        if self._fd:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                self._fd.close()
            except Exception:
                pass
            self._fd = None
        try:
            os.remove(self._path)
        except OSError:
            pass


# ─── Graceful shutdown ────────────────────────────────────────────────────────

class ShutdownFlag:
    """Cooperative shutdown flag set by signal handlers."""

    def __init__(self):
        self.requested = False

    def request(self):
        self.requested = True

    def install(self, loop: asyncio.AbstractEventLoop):
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle, sig)

    def _handle(self, sig):
        logger.info(f"Received {signal.Signals(sig).name} — finishing in-flight sites then exiting")
        self.requested = True


# ─── Retry helper ─────────────────────────────────────────────────────────────

async def _retry_webhook(coro_factory, description: str = "webhook call"):
    """Retry a webhook call with capped exponential backoff.

    coro_factory: a zero-arg callable that returns a fresh awaitable each time.
    Returns True on first success, False after all retries are exhausted.
    """
    for attempt in range(1, TRANSIENT_RETRY_MAX + 1):
        try:
            result = await coro_factory()
            if result:
                return True
            if isinstance(result, dict) and not result.get("retryable", False):
                logger.warning(f"[retry] {description} — non-retryable failure")
                return False
        except Exception as e:
            logger.warning(f"[retry] {description} attempt {attempt} error: {e}")
        if attempt < TRANSIENT_RETRY_MAX:
            delay = min(TRANSIENT_RETRY_BASE_DELAY * (2 ** (attempt - 1)), 10.0)
            logger.info(f"[retry] {description} — retrying in {delay:.1f}s (attempt {attempt}/{TRANSIENT_RETRY_MAX})")
            await asyncio.sleep(delay)
    logger.error(f"[retry] {description} — all {TRANSIENT_RETRY_MAX} attempts failed")
    return False


async def _retry_result(coro_factory, success_fn, description: str = "call"):
    """Retry a call that returns a structured result object."""
    last_result = None
    for attempt in range(1, TRANSIENT_RETRY_MAX + 1):
        try:
            result = await coro_factory()
            last_result = result
            if success_fn(result):
                return result
            if isinstance(result, dict) and not result.get("retryable", False):
                logger.warning(f"[retry] {description} — non-retryable failure")
                return result
        except Exception as e:
            logger.warning(f"[retry] {description} attempt {attempt} error: {e}")
        if attempt < TRANSIENT_RETRY_MAX:
            delay = min(TRANSIENT_RETRY_BASE_DELAY * (2 ** (attempt - 1)), 10.0)
            logger.info(f"[retry] {description} — retrying in {delay:.1f}s (attempt {attempt}/{TRANSIENT_RETRY_MAX})")
            await asyncio.sleep(delay)
    logger.error(f"[retry] {description} — all {TRANSIENT_RETRY_MAX} attempts failed")
    return last_result


async def main_from_sheet(batch_size: int = 50, dry_run: bool = False):
    start_time = time.time()
    logger.info("Fetching URLs from source sheet (alpha/beta/gamma)...")

    output_dir = os.path.dirname(os.path.abspath(__file__))
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    checkpoint_path = os.path.join(output_dir, "checkpoint_latest.json")
    checkpoint_archive_path = os.path.join(output_dir, f"checkpoint_{run_id}.json")
    summary_path = os.path.join(output_dir, f"run_summary_{run_id}.json")
    lock_path = os.path.join(output_dir, ".scraper_run.lock")

    # ── Run lock: refuse to start if another run is active ──
    run_lock = RunLock(lock_path, run_id)
    if not run_lock.acquire():
        logger.error("Another scraper run is already active (lock held). Exiting.")
        try:
            with open(lock_path, "r") as f:
                logger.error(f"Lock info: {f.read().strip()}")
        except Exception:
            pass
        return
    logger.info(f"Run lock acquired: {run_id} (pid {os.getpid()})")

    # ── Graceful shutdown handler ──
    shutdown = ShutdownFlag()
    try:
        loop = asyncio.get_running_loop()
        shutdown.install(loop)
    except Exception:
        pass

    checkpoint_state = _load_checkpoint_state(checkpoint_path)

    # Counters for summary
    posted_count = 0
    write_failures = 0
    mark_done_count = 0
    mark_error_count = 0
    mark_fail_count = 0
    timeout_count = 0
    blocked_count = 0

    try:
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

            resume_mark_only = []
            pending_sites = []
            for site_info in url_list:
                action = _checkpoint_resume_action(site_info, checkpoint_state)
                if action == "mark_only":
                    resume_mark_only.append(site_info)
                else:
                    pending_sites.append(site_info)

            # ── Resume: mark-only for sites already written ──
            for site_info in resume_mark_only:
                key = _checkpoint_key(site_info)
                entry = checkpoint_state.get(key, {})
                mark_ok = False
                if not dry_run and site_info.get("tab") and site_info.get("row"):
                    mark_result = await _retry_result(
                        lambda si=site_info: GoogleClient.mark_source_processed_detailed(
                            session, [{"tab": si["tab"], "row": si["row"], "status": "done"}]
                        ),
                        lambda res: bool(res and res.get("ok")),
                        description=f"mark_done resume {site_info.get('url', '')}"
                    )
                    mark_ok = bool(mark_result and mark_result.get("ok"))
                entry.update({
                    "checkpoint_key": key,
                    "website": site_info.get("url") or site_info.get("website") or "",
                    "tab": site_info.get("tab", ""),
                    "row": site_info.get("row", ""),
                    "scrape": entry.get("scrape", "ok"),
                    "write": "ok",
                    "marked": "done" if (mark_ok or dry_run) else "mark_failed",
                    "timestamp": datetime.now().isoformat(),
                })
                if mark_ok:
                    mark_done_count += 1
                checkpoint_state[key] = entry
                _save_checkpoint_state(checkpoint_path, checkpoint_state)
                logger.info(f"[resume] {entry['website']} — skipped scrape, retried mark-only")

            logger.info(f"Starting batch processing of {len(pending_sites)} sites (concurrency: {CONCURRENT_SITES})")
            if dry_run:
                logger.info("DRY RUN: will scrape but NOT write to output sheet")

            person_extractor = PersonExtractor()
            semaphore = asyncio.Semaphore(CONCURRENT_SITES)
            all_results = []
            site_iter = iter(pending_sites)
            active_tasks: Dict[asyncio.Task, dict] = {}
            started_site_count = 0
            shutdown_announced = False

            # ── Per-company checkpointed writes ──────────────────────────
            checkpoint_log = dict(checkpoint_state)

            # Buffer for chunked output writes
            write_buffer = []  # list of (company_entry, result, entry_log, checkpoint_key)

            def _start_next_site() -> bool:
                nonlocal started_site_count
                if shutdown.requested:
                    return False
                try:
                    site_info = next(site_iter)
                except StopIteration:
                    return False
                task = asyncio.create_task(process_single_site(session, site_info, semaphore, person_extractor))
                active_tasks[task] = site_info
                started_site_count += 1
                return True

            async def _flush_write_buffer():
                """Write buffered companies in one chunk, then mark source rows."""
                nonlocal posted_count, write_failures, mark_done_count, mark_fail_count
                if not write_buffer:
                    return
                chunk_entries = [item[0] for item in write_buffer]
                chunk_ok_statuses = {"inserted", "updated", "duplicate_skipped", "legacy_success"}

                write_result = await _retry_result(
                    lambda ce=chunk_entries: GoogleClient.post_to_output_detailed(session, ce),
                    lambda res: bool(res and res.get("ok")),
                    description=f"output_write chunk of {len(chunk_entries)}"
                )
                result_map = {
                    (item.get("id") or "").strip(): item
                    for item in (write_result or {}).get("results", [])
                }

                for company_entry, result, entry_log, ck in write_buffer:
                    website = result.get("input_website", "unknown")
                    company_result = result_map.get(company_entry["id"], {"status": "failed"})
                    company_status = (company_result.get("status") or "failed").strip()
                    if company_status in chunk_ok_statuses:
                        entry_log["write"] = "ok"
                        posted_count += 1
                        logger.info(f"[checkpoint] {website} — written to Employee_db ({company_status})")
                        if result.get("tab") and result.get("row"):
                            mark_result = await _retry_result(
                                lambda r=result: GoogleClient.mark_source_processed_detailed(
                                    session, [{"tab": r["tab"], "row": r["row"], "status": "done"}]
                                ),
                                lambda res: bool(res and res.get("ok")),
                                description=f"mark_done {website}"
                            )
                            m_ok = bool(mark_result and mark_result.get("ok"))
                            entry_log["marked"] = "done" if m_ok else "mark_failed"
                            if m_ok:
                                mark_done_count += 1
                            else:
                                mark_fail_count += 1
                        else:
                            entry_log["marked"] = "no_source_row"
                    else:
                        entry_log["write"] = "failed"
                        entry_log["marked"] = "skipped"
                        write_failures += 1
                        logger.error(
                            f"[checkpoint] {website} — write FAILED ({company_status}), "
                            f"source row NOT marked done"
                        )
                    checkpoint_log[ck] = entry_log
                _save_checkpoint_state(checkpoint_path, checkpoint_log)
                write_buffer.clear()

            while len(active_tasks) < CONCURRENT_SITES and _start_next_site():
                pass

            while active_tasks:
                done, _pending = await asyncio.wait(
                    list(active_tasks.keys()),
                    return_when=asyncio.FIRST_COMPLETED
                )

                for task in done:
                    active_tasks.pop(task, None)
                    try:
                        result = await task
                    except asyncio.CancelledError:
                        continue

                    all_results.append(result)
                    website = result.get("input_website", "unknown")
                    checkpoint_key = _checkpoint_key(result)
                    entry_log = {
                        "checkpoint_key": checkpoint_key,
                        "website": website,
                        "tab": result.get("tab", ""),
                        "row": result.get("row", ""),
                        "scrape": "error" if result.get("error") else "ok",
                        "write": None,
                        "marked": None,
                        "timestamp": datetime.now().isoformat(),
                    }

                    error_msg = result.get("error", "")
                    if "timed out" in error_msg:
                        timeout_count += 1
                    if result.get("blocked") or "block" in error_msg.lower():
                        blocked_count += 1

                    if result.get("error"):
                        entry_log["write"] = "skipped"
                        entry_log["marked"] = "error"
                        if not dry_run and result.get("tab") and result.get("row"):
                            mark_result = await _retry_result(
                                lambda r=result: GoogleClient.mark_source_processed_detailed(
                                    session, [{"tab": r["tab"], "row": r["row"], "status": "error"}]
                                ),
                                lambda res: bool(res and res.get("ok")),
                                description=f"mark_error {website}"
                            )
                            m_ok = bool(mark_result and mark_result.get("ok"))
                            entry_log["marked"] = "error" if m_ok else "mark_failed"
                            if m_ok:
                                mark_error_count += 1
                            else:
                                mark_fail_count += 1
                        checkpoint_log[checkpoint_key] = entry_log
                        _save_checkpoint_state(checkpoint_path, checkpoint_log)
                        logger.warning(f"[checkpoint] {website} — scrape error, marked error")
                        continue

                    company_name = result.get("name") or urllib.parse.urlparse(website).netloc
                    company_id = generate_company_id(company_name)
                    company_entry = {
                        "id": company_id,
                        "name": company_name,
                        "website": website,
                        "linkedin": result.get("company_linkedin", ""),
                        "class": result.get("tab", ""),
                        "persons": [
                            {"name": p.get("name", ""), "role": p.get("role", ""),
                             "email": p.get("email", ""), "linkedin": p.get("linkedin", "")}
                            for p in result.get("persons", [])
                        ]
                    }

                    if dry_run:
                        entry_log["write"] = "dry_run"
                        entry_log["marked"] = "dry_run"
                        checkpoint_log[checkpoint_key] = entry_log
                        _save_checkpoint_state(checkpoint_path, checkpoint_log)
                        posted_count += 1
                        continue

                    write_buffer.append((company_entry, result, entry_log, checkpoint_key))
                    if len(write_buffer) >= OUTPUT_WRITE_CHUNK_SIZE:
                        await _flush_write_buffer()

                if shutdown.requested and not shutdown_announced:
                    logger.info("[shutdown] Graceful shutdown requested — no new sites will be started")
                    shutdown_announced = True

                if not shutdown.requested:
                    while len(active_tasks) < CONCURRENT_SITES and _start_next_site():
                        pass

            # Flush remaining buffer
            await _flush_write_buffer()

            # ── Save full scrape results as before ──
            output_path = os.path.join(output_dir, "linkedin_results.json")
            with open(output_path, "w") as f:
                json.dump(all_results, f, indent=2, default=str)

            # ── Final checkpoint write ──
            _save_checkpoint_state(checkpoint_path, checkpoint_log)
            _save_checkpoint_state(checkpoint_archive_path, checkpoint_log)

            elapsed = time.time() - start_time
            scrape_ok = sum(1 for r in all_results if not r.get("error"))
            scrape_err = sum(1 for r in all_results if r.get("error"))
            total_linkedin = sum(len(r.get("linkedin_urls", [])) for r in all_results)
            total_emails = sum(len(r.get("emails", [])) for r in all_results)
            total_persons = sum(len(r.get("persons", [])) for r in all_results)
            processed_site_count = started_site_count
            per_site_seconds = (elapsed / processed_site_count) if processed_site_count else 0.0

            # ── Human-readable log summary ──
            logger.info("")
            logger.info(f"{'='*60}")
            logger.info(f"BATCH COMPLETE — run {run_id}")
            logger.info(f"{'='*60}")
            logger.info(f"   Sites attempted:   {processed_site_count}")
            logger.info(f"   Sites scraped OK:  {scrape_ok}/{processed_site_count}")
            logger.info(f"   Scrape errors:     {scrape_err}")
            logger.info(f"   Written to sheet:  {posted_count}")
            logger.info(f"   Write failures:    {write_failures}")
            logger.info(f"   Marked done:       {mark_done_count}")
            logger.info(f"   Marked error:      {mark_error_count}")
            logger.info(f"   Mark failures:     {mark_fail_count}")
            logger.info(f"   Timeouts:          {timeout_count}")
            logger.info(f"   Blocked:           {blocked_count}")
            logger.info(f"   LinkedIn URLs:     {total_linkedin}")
            logger.info(f"   Emails:            {total_emails}")
            logger.info(f"   Persons extracted: {total_persons}")
            logger.info(f"   Time elapsed:      {elapsed:.1f}s ({per_site_seconds:.1f}s per site)")
            if shutdown.requested:
                logger.info(f"   Shutdown:          graceful (active sites drained, no new sites started)")
            logger.info(f"{'='*60}")
            logger.info(f"Results:    {output_path}")
            logger.info(f"Checkpoint: {checkpoint_path}")
            logger.info(f"Archive:    {checkpoint_archive_path}")

            # ── Machine-readable JSON summary ──
            run_summary = {
                "run_id": run_id,
                "started": datetime.fromtimestamp(start_time).isoformat(),
                "finished": datetime.now().isoformat(),
                "elapsed_seconds": round(elapsed, 1),
                "per_site_seconds": round(per_site_seconds, 1),
                "dry_run": dry_run,
                "graceful_shutdown": shutdown.requested,
                "batch_size": batch_size,
                "attempted": processed_site_count,
                "resumed_mark_only": len(resume_mark_only),
                "scraped_ok": scrape_ok,
                "scraped_error": scrape_err,
                "written_ok": posted_count,
                "write_failed": write_failures,
                "marked_done": mark_done_count,
                "marked_error": mark_error_count,
                "mark_failed": mark_fail_count,
                "timeout_count": timeout_count,
                "blocked_count": blocked_count,
                "linkedin_urls": total_linkedin,
                "emails": total_emails,
                "persons_extracted": total_persons,
            }
            _save_checkpoint_state(summary_path, run_summary)
            logger.info(f"Summary:    {summary_path}")

    finally:
        run_lock.release()
        logger.info(f"Run lock released: {run_id}")


async def main(urls: List[str]):
    start_time = time.time()
    logger.info(f"Starting batch processing of {len(urls)} sites (concurrency: {CONCURRENT_SITES})")

    person_extractor = PersonExtractor()
    semaphore = asyncio.Semaphore(CONCURRENT_SITES)

    async with aiohttp.ClientSession() as google_session:
        tasks = [
            process_single_site(google_session, url, semaphore, person_extractor)
            for url in urls
        ]
        all_results = await asyncio.gather(*tasks)

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
