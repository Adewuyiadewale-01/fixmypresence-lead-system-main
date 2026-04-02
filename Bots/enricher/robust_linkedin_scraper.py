import asyncio
import re
import ssl
import urllib.parse
import html
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Optional, Set, Dict, Any
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
CONCURRENT_SITES = 10  # Number of sites to process simultaneously
PLAYWRIGHT_LIMIT = 6  # Balanced: enough for team pages
SUBPAGE_LIMIT = 40    # Balanced: check key pages without crawling everything  
LEVEL2_LIMIT = 30     # Balanced: check profile pages found on team page
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# PASTE YOUR GOOGLE WEB APP URL HERE
# GOOGLE_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbyLmYnBgyBBMGGrDnp15yn-Xf0PC0xNTCuLosWiFpsiKtIhm1_9RL2SqEoNSf7iA6txEA/exec"

# Google Doc Webhook URL (for enriched data with names/roles)
# Deploy google_doc_integration.js as a Web App and paste the URL here
GOOGLE_DOC_WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbxPUEOSvwpFNEdcFWRdI19_kw6TiNEgwUuufOJ3_VcL8EP2J4tC2fl_ZJ9xog3DlHiv/exec"


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class PersonContact:
    """Represents a person found on a company website with contextual information."""
    linkedin_url: str
    name: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None
    confidence: float = 0.0  # 0.0-1.0 confidence score for extracted data
    source_page: Optional[str] = None  # Page where the contact was found
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "linkedin_url": self.linkedin_url,
            "name": self.name,
            "role": self.role,
            "email": self.email,
            "confidence": round(self.confidence, 2),
            "source_page": self.source_page
        }


@dataclass 
class ScrapeResult:
    """Result of scraping a single website."""
    website: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    contacts: List[PersonContact] = field(default_factory=list)
    orphan_emails: List[str] = field(default_factory=list)  # Emails not linked to a person
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "website": self.website,
            "timestamp": self.timestamp,
            "contacts": [c.to_dict() for c in self.contacts],
            "orphan_emails": self.orphan_emails
        }


# =============================================================================
# REGEX PATTERNS
# =============================================================================

RE_LINKEDIN_URL = re.compile(
    # Modified to capture URLs inside text/JS/attributes by looking for the protocol and domain.
    # We relax the ending characters to include quotes/spaces as boundaries for extraction.
    # Also updated to handle country subdomains like nl.linkedin.com
    r'(https?:\/\/(?:[a-z0-9-]+\.)?linkedin\.com\/(?:in|company|showcase|school)\/[a-zA-Z0-9%\-_.\\/]+)',
    re.I
)

# Email regex pattern - captures standard email formats
RE_EMAIL = re.compile(
    r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
    re.I
)

# =============================================================================
# ROLE KEYWORDS - Multi-language support for role extraction
# =============================================================================
# Organized by category for maintainability. Sorted by length (longest first)
# within each language to ensure "Managing Director" matches before "Director".

ROLE_KEYWORDS = {
    "en": [
        # C-Level / Executive (most specific first)
        "chief executive officer", "chief financial officer", "chief operating officer",
        "chief technology officer", "chief marketing officer", "chief investment officer",
        "chief information officer", "chief human resources officer", "chief digital officer",
        "chief commercial officer", "chief revenue officer", "chief strategy officer",
        "chief legal officer", "chief compliance officer", "chief risk officer",
        "chief people officer", "chief product officer", "chief data officer",
        "chief executive", "chief financial", "chief operating",
        "ceo", "cfo", "coo", "cto", "cmo", "cio", "chro", "cdo", "cco", "cro", "cso", "clo", "cpo",
        
        # Directors (specific before generic)
        "non-executive director", "executive director", "managing director",
        "investment director", "finance director", "operations director",
        "commercial director", "marketing director", "sales director",
        "hr director", "human resources director", "it director", "legal director",
        "portfolio director", "regional director", "group director",
        "deputy director", "assistant director", "associate director",
        "director of operations", "director of finance", "director of investments",
        "director of strategy", "director of business development",
        "director general", "director",
        
        # Partners (specific before generic)
        "managing partner", "senior partner", "equity partner", "founding partner",
        "investment partner", "operating partner", "general partner", "limited partner",
        "junior partner", "partner",
        
        # Founders
        "co-founder & ceo", "co-founder and ceo", "founder & ceo", "founder and ceo",
        "co-founder & managing director", "co-founder",
        "cofounder", "founder",
        
        # President / VP
        "executive vice president", "senior vice president", "group vice president",
        "regional vice president", "vice president of operations",
        "vice president of finance", "vice president of investments",
        "vice president", "president", "evp", "svp", "vp",
        
        # Chairman / Board
        "executive chairman", "non-executive chairman", "deputy chairman",
        "vice chairman", "chairman of the board",
        "chairwoman", "chairman", "chair",
        "board member", "board of directors", "supervisory board member",
        "member of the board",
        
        # Investment / Finance roles
        "investment manager", "portfolio manager", "fund manager", "asset manager",
        "investment analyst", "portfolio analyst", "financial analyst",
        "investment associate", "investment professional",
        "private equity professional", "venture capital associate",
        "deal partner", "deal team member",
        
        # Heads
        "global head", "regional head", "head of investments", "head of finance",
        "head of operations", "head of compliance", "head of risk",
        "head of legal", "head of hr", "head of marketing", "head of sales",
        "head of business development", "head of strategy", "head of it",
        "head of technology", "head of research", "head of product",
        "department head", "team head", "head of",
        
        # Manager roles
        "general manager", "senior manager", "operations manager", "finance manager",
        "investment manager", "project manager", "relationship manager",
        "client manager", "account manager", "business development manager",
        "programme manager", "program manager", "manager",
        
        # Principal / Advisor
        "senior principal", "principal", 
        "senior advisor", "senior adviser", "special advisor", "advisor", "adviser",
        "senior consultant", "management consultant", "consultant",
        
        # Associates / Analysts
        "senior associate", "investment associate", "associate director", "associate",
        "senior analyst", "financial analyst", "research analyst", "analyst",
        
        # Other
        "secretary", "company secretary", "treasurer", "controller",
        "owner", "proprietor", "entrepreneur",
    ],
    
    "nl": [
        # Directie / Bestuur (most specific first)
        "algemeen directeur", "managing director", "uitvoerend directeur",
        "financieel directeur", "operationeel directeur", "commercieel directeur",
        "technisch directeur", "directeur strategie", "directeur investeringen",
        "interim directeur", "adjunct-directeur", "plaatsvervangend directeur",
        "directeur bedrijfsvoering", "directeur operations",
        "statutory director", "directeur",
        
        # Bestuurder
        "bestuurslid", "lid van de raad van bestuur", "raad van bestuur",
        "lid directie", "directielid", "bestuurder",
        
        # Partner
        "managing partner", "senior partner", "vennoot", "beherend vennoot",
        "medevennoot", "partner",
        
        # Oprichter
        "medeoprichter en ceo", "mede-oprichter en ceo",
        "oprichter en ceo", "medeoprichter", "mede-oprichter", "oprichter",
        
        # Voorzitter
        "voorzitter raad van commissarissen", "voorzitter directie",
        "vice-voorzitter", "vicevoorzitter", "voorzitter",
        
        # Commissaris
        "lid raad van commissarissen", "commissaris",
        
        # Investment / Finance roles (often English in Dutch finance)
        "investment manager", "portfolio manager", "fund manager",
        "investment director", "investment analyst",
        
        # Hoofd / Manager
        "afdelingshoofd", "hoofd financiën", "hoofd operaties",
        "hoofd juridische zaken", "hoofd hr", "hoofd marketing",
        "hoofd verkoop", "hoofd strategie", "hoofd",
        "general manager", "senior manager", "operations manager",
        "manager",
        
        # Adviseur
        "senior adviseur", "financieel adviseur", "strategisch adviseur",
        "adviseur",
        
        # Consultant
        "senior consultant", "management consultant", "consultant",
        
        # Analist / Associate
        "senior analist", "financieel analist", "investment analist",
        "senior associate", "associate", "analist",
        
        # Other
        "secretaris", "penningmeester", "controller",
        "eigenaar", "mede-eigenaar", "ondernemer",
    ]
}

# Flatten and sort by length (longest first) for matching
_ALL_ROLE_KEYWORDS = []
for lang_keywords in ROLE_KEYWORDS.values():
    _ALL_ROLE_KEYWORDS.extend(lang_keywords)
# Remove duplicates while preserving order, sort by length descending
_ALL_ROLE_KEYWORDS = sorted(set(_ALL_ROLE_KEYWORDS), key=lambda x: -len(x))


def extract_role_from_context(text: str, radius: int = 300) -> Optional[str]:
    """
    Extract role from text by matching against known role keywords.
    Uses longest-match-first strategy to prefer specific roles over generic ones.
    
    Args:
        text: The text to scan for role keywords
        radius: Not used currently but kept for API compatibility
        
    Returns:
        The matched role (properly capitalized) or None
    """
    if not text:
        return None
    
    # Normalize text for matching
    text_lower = text.lower()
    # Clean up extra whitespace
    text_lower = ' '.join(text_lower.split())
    
    # Try to match keywords (already sorted by length, longest first)
    for keyword in _ALL_ROLE_KEYWORDS:
        # Use word boundary matching
        pattern = r'\b' + re.escape(keyword) + r'\b'
        match = re.search(pattern, text_lower)
        if match:
            # Return the matched text with proper capitalization
            # Get the actual matched text from original
            start, end = match.span()
            # Find corresponding position in original text
            original_match = text[start:end] if start < len(text) else keyword.title()
            # Clean up and title case
            return keyword.title()
    
    return None


# Legacy regex pattern kept for backward compatibility
RE_ROLE_KEYWORDS = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in _ALL_ROLE_KEYWORDS[:50]) + r')\b',
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
    "/team-van", "/onze-mensen", "/organisatie", "/medewerkers",
    # Nested Dutch team pages  
    "/over-ons/team", "/over-ons/onze-mensen", "/over-ons/medewerkers", "/over-ons/adviseurs",
    "/our-team", "/the-team", "/people", "/staff", "/employees", "/leadership",
    "/management", "/partners", "/advisors", "/professionals"
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

    def _parse_name_role_from_text(self, text: str) -> tuple[Optional[str], Optional[str], float]:
        """
        Parse name and role from a text string like "John Smith, CEO" or "Jane Doe - Managing Director".
        Returns: (name, role, confidence)
        """
        if not text or len(text) > 200:  # Skip overly long text
            return None, None, 0.0
        
        text = text.strip()
        
        # Check for role keywords in the text
        role_match = RE_ROLE_KEYWORDS.search(text)
        
        if role_match:
            # Split by common separators
            separators = [',', ' - ', ' – ', ' | ', ' · ', '\n', '\t']
            parts = None
            for sep in separators:
                if sep in text:
                    parts = text.split(sep, 1)
                    break
            
            if parts and len(parts) == 2:
                # Determine which part is name and which is role
                part1, part2 = parts[0].strip(), parts[1].strip()
                if RE_ROLE_KEYWORDS.search(part2):
                    return part1, part2, 0.85
                elif RE_ROLE_KEYWORDS.search(part1):
                    return part2, part1, 0.85
            
            # Role found but no clear separator - use full text as context
            return None, role_match.group(0), 0.5
        
        # No role found - if short text, might be a name
        if len(text) < 50 and ' ' in text:
            # Basic name validation: should have 2-4 words, capitalized
            words = text.split()
            if 2 <= len(words) <= 4 and all(w[0].isupper() for w in words if w):
                return text, None, 0.4
        
        return None, None, 0.0

    def _extract_context_from_anchor(self, anchor_tag, linkedin_url: str, source_page: str = None) -> PersonContact:
        """Extract name and role from an anchor tag and its surrounding context."""
        contact = PersonContact(linkedin_url=linkedin_url, source_page=source_page)
        
        # Strategy 1: Check anchor text itself
        anchor_text = anchor_tag.get_text(strip=True) if hasattr(anchor_tag, 'get_text') else str(anchor_tag)
        
        # Skip if anchor text is just "LinkedIn" or similar
        skip_texts = {'linkedin', 'view profile', 'profile', 'connect', 'follow', 'lees meer', 'read more', ''}
        if anchor_text.lower() not in skip_texts:
            name, role, conf = self._parse_name_role_from_text(anchor_text)
            if name:
                contact.name = name
                contact.confidence = max(contact.confidence, conf)
            if role:
                contact.role = role
                contact.confidence = max(contact.confidence, conf)
        
        # Strategy 2: Look at parent container for more context
        if hasattr(anchor_tag, 'find_parent'):
            parent = anchor_tag.find_parent(['div', 'li', 'article', 'section', 'td', 'tr'])
            if parent:
                # Find headings (likely names)
                if not contact.name:
                    heading = parent.find(['h1', 'h2', 'h3', 'h4', 'h5', 'strong'])
                    if heading:
                        heading_text = heading.get_text(strip=True)
                        if heading_text and len(heading_text) < 80:
                            # Validate it looks like a name (not a section title)
                            if not any(kw in heading_text.lower() for kw in ['team', 'about', 'contact', 'our', 'ons']):
                                contact.name = heading_text
                                contact.confidence = max(contact.confidence, 0.7)
                
                # Find role elements by class patterns
                if not contact.role:
                    role_classes = ['role', 'title', 'position', 'job', 'functie', 'function']
                    for cls in role_classes:
                        role_el = parent.find(class_=re.compile(cls, re.I))
                        if role_el:
                            role_text = role_el.get_text(strip=True)
                            if role_text and len(role_text) < 100:
                                contact.role = role_text
                                contact.confidence = max(contact.confidence, 0.65)
                                break
                
                # Fallback: look for role keywords in parent text
                if not contact.role:
                    parent_text = parent.get_text(strip=True)
                    role_match = RE_ROLE_KEYWORDS.search(parent_text)
                    if role_match:
                        # Try to extract a reasonable role string around the match
                        start = max(0, role_match.start() - 20)
                        end = min(len(parent_text), role_match.end() + 30)
                        role_context = parent_text[start:end].strip()
                        # Clean up
                        role_context = re.sub(r'\s+', ' ', role_context)
                        if len(role_context) < 60:
                            contact.role = role_context
                            contact.confidence = max(contact.confidence, 0.5)
        
        return contact

    def _extract_from_soup_with_context(self, soup: BeautifulSoup, base_url: str, source_page: str = None) -> List[PersonContact]:
        """Extracts LinkedIn URLs with context (name/role) from a BeautifulSoup object."""
        contacts = []
        seen_urls = set()
        
        # 1. Check all <a> tags
        for a in soup.find_all('a', href=True):
            href = a['href']
            full_url = urllib.parse.urljoin(base_url, href)
            if 'linkedin.com' in full_url:
                cleaned = self._clean_url(full_url)
                if cleaned and cleaned not in seen_urls:
                    seen_urls.add(cleaned)
                    contact = self._extract_context_from_anchor(a, cleaned, source_page)
                    contacts.append(contact)

        # 2. Check data attributes
        for tag in soup.find_all(True):
            for attr in ['data-href', 'data-url', 'content']:
                if tag.has_attr(attr):
                    val = tag[attr]
                    if 'linkedin.com' in val:
                        cleaned = self._clean_url(val)
                        if cleaned and cleaned not in seen_urls:
                            seen_urls.add(cleaned)
                            contact = self._extract_context_from_anchor(tag, cleaned, source_page)
                            contacts.append(contact)
        
        return contacts

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

    async def _fetch_with_playwright_fallback(self, session: aiohttp.ClientSession, url: str) -> tuple[Set[str], Set[str]]:
        """
        Tries static fetch first, falls back to Playwright if static fails or gets blocked.
        This ensures we don't lose profiles from sites with WAF protection.
        """
        # Try static first (fast)
        found, emails = await self._fetch_static(session, url)
        
        # If static succeeded and found something, return it
        if found or emails:
            return found, emails
        
        # Static failed or returned empty - try Playwright fallback
        try:
            pw_found, _, pw_emails = await self._process_playwright(url)
            if pw_found or pw_emails:
                logger.debug(f"Playwright fallback succeeded for {url}: {len(pw_found)} links")
            return pw_found, pw_emails
        except Exception as e:
            logger.debug(f"Playwright fallback also failed for {url}: {e}")
            return set(), set()

    async def _fetch_static_with_context(self, session: aiohttp.ClientSession, url: str) -> tuple[List[PersonContact], Set[str]]:
        """
        Layer 3 (Structure): Fetches a page statically with BeautifulSoup and extracts 
        LinkedIn contacts WITH context (names/roles). Fast and parallel.
        """
        try:
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            async with session.get(url, headers={"User-Agent": USER_AGENT}, timeout=10, ssl=ssl_context) as response:
                if response.status != 200:
                    return [], set()
                text = await response.text()
                soup = BeautifulSoup(text, 'html.parser')
                
                # Use existing method for context extraction
                contacts = self._extract_from_soup_with_context(soup, str(response.url), url)
                
                # Also do regex fallback to catch any hidden URLs
                for match in RE_LINKEDIN_URL.findall(text):
                    cleaned = self._clean_url(match)
                    if cleaned and any(x in cleaned for x in ['/in/', '/company/', '/showcase/']):
                        if not any(c.linkedin_url == cleaned for c in contacts):
                            contacts.append(PersonContact(linkedin_url=cleaned, source_page=url))
                
                # Extract emails
                emails = self._extract_emails_from_text(text)
                return contacts, emails
        except Exception as e:
            logger.debug(f"Static context fetch failed for {url}: {e}")
            return [], set()

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
                         logger.info(f"    -> Found {len(level2_links)} potential profile links on {url}. Checking with fallback...")
                         l2_tasks = [self._fetch_with_playwright_fallback(session, l2) for l2 in level2_links]
                         l2_results = await asyncio.gather(*l2_tasks)
                         for res2, emails2 in l2_results:
                                all_found.update(res2)
                                all_emails.update(emails2)
                    
                else:
                    # Static check with Playwright fallback for WAF-protected sites
                    tasks.append(self._fetch_with_playwright_fallback(session, url))

            if tasks:
                results_list = await asyncio.gather(*tasks)
                for res, emails in results_list:
                    all_found.update(res)
                    all_emails.update(emails)

        # Post-processing filtration
        # The prompt asks to "scrape every linkedin links", so we return all.
        
        return sorted(list(all_found)), sorted(list(all_emails))

    async def find_contacts_with_context(self, start_url: str) -> ScrapeResult:
        """
        Hybrid method with 3-phase defense penetration:
        
        Phase 1 (Compass): Smart navigation with blocklist/allowlist to find profile pages
        Phase 2 (Sniper): Enhanced JS extraction with icon detection and parent climbing
        Phase 3 (Vacuum): Regex fallback on raw HTML to catch any missed URLs
        
        This ensures maximum LinkedIn URL coverage plus names/roles where available.
        """
        if not start_url.startswith('http'):
            start_url = 'https://' + start_url

        result = ScrapeResult(website=start_url)
        contacts_by_url: Dict[str, PersonContact] = {}
        all_emails: Set[str] = set()
        
        parsed = urllib.parse.urlparse(start_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        
        # =====================================================================
        # PHASE 1: THE COMPASS - Smart Navigation Logic
        # =====================================================================
        logger.info(f"[Context] Phase 1 (Compass): Discovering profile pages...")
        
        # Blocklist - URLs to IGNORE (news, blog, etc.)
        BLOCKLIST_PATTERNS = ['insights', 'news', 'blog', 'press', 'article', 'events', 
                              'careers', 'jobs', 'cookie', 'privacy', 'terms', 'legal']
        
        # Allowlist - URLs to PRIORITIZE (team pages)
        ALLOWLIST_PATTERNS = ['our-people', 'our-team', 'team', 'people', 'leadership',
                              'about-us', 'about', 'over-ons', 'wie-zijn-wij', 'staff',
                              'professionals', 'partners', 'advisors', 'executives']
        
        # JavaScript to find profile page links on team pages
        FIND_PROFILE_LINKS_JS = """
        () => {
            const profiles = [];
            const seen = new Set();
            
            // Blocklist patterns
            const blocklist = ['insights', 'news', 'blog', 'press', 'article', 'events', 
                               'careers', 'jobs', 'cookie', 'privacy', 'terms', 'legal'];
            
            // Allowlist patterns (profile-like paths)
            const allowlist = ['our-people', 'team', 'people', 'staff', 'member', 
                               'professional', 'partner', 'advisor', 'executive', 'leadership'];
            
            const allLinks = document.querySelectorAll('a[href]');
            
            for (const link of allLinks) {
                const href = link.href;
                if (!href || seen.has(href)) continue;
                
                const url = new URL(href);
                const path = url.pathname.toLowerCase();
                
                // Skip blocklist
                if (blocklist.some(b => path.includes(b))) continue;
                
                // Skip external links
                if (url.origin !== window.location.origin) continue;
                
                // Skip non-HTML resources
                if (/\\.(pdf|jpg|jpeg|png|gif|doc|docx|xlsx)$/i.test(path)) continue;
                
                // Check for profile-like patterns
                const isProfileLike = allowlist.some(a => path.includes(a));
                
                // Also check if link has a person's name (image with name nearby)
                const hasPersonImage = link.querySelector('img') || link.closest('[class*="team"]');
                const nearbyName = link.innerText?.trim() || 
                                   link.closest('div,article,li')?.querySelector('h2,h3,h4,strong')?.innerText;
                
                // Count path segments (exclude root and query)
                const segments = path.split('/').filter(s => s && s.length > 0);
                
                // Accept if: has allowlist pattern, OR looks like a person link
                if (isProfileLike || (hasPersonImage && nearbyName && segments.length >= 2)) {
                    seen.add(href);
                    profiles.push({
                        url: href,
                        text: nearbyName || '',
                        isProfilePage: segments.length >= 2 && isProfileLike
                    });
                }
            }
            
            // Sort: profile pages first, then others
            return profiles.sort((a, b) => (b.isProfilePage ? 1 : 0) - (a.isProfilePage ? 1 : 0)).slice(0, 50);
        }
        """
        
        # =====================================================================
        # PHASE 2: THE SNIPER - Enhanced Extraction with Icon Detection
        # =====================================================================
        
        # Enhanced JavaScript to extract LinkedIn links with icon detection
        EXTRACT_CONTACTS_SNIPER_JS = """
        () => {
            const contacts = [];
            const seenUrls = new Set();
            
            // Find all LinkedIn links (including icon-only links, company pages, and profiles)
            const linkedinLinks = document.querySelectorAll('a[href*="linkedin.com/in/"], a[href*="linkedin.com/company/"], a[href*="linkedin.com/showcase/"]');
            
            for (const link of linkedinLinks) {
                const href = link.href.split('?')[0].replace(/\\/+$/, '');
                if (!href || seenUrls.has(href)) continue;
                seenUrls.add(href);
                
                let name = null;
                let role = null;
                let confidence = 0;
                
                // SNIPER Strategy 1: Check anchor text (skip if it's icon-only)
                const anchorText = link.innerText?.trim();
                if (anchorText && anchorText.length > 2 && anchorText.length < 80) {
                    if (!anchorText.toLowerCase().includes('linkedin') && 
                        !anchorText.toLowerCase().includes('view profile') &&
                        !anchorText.toLowerCase().includes('connect with')) {
                        name = anchorText;
                        confidence = 0.6;
                    }
                }
                
                // SNIPER Strategy 2: Icon Detection - Link has image but no text
                const hasImage = link.querySelector('img, svg, i[class*="linkedin"]');
                const isIconOnly = hasImage && (!anchorText || anchorText.length < 3);
                
                // SNIPER Strategy 3: Parent Climbing for context
                let parent = link.parentElement;
                let depth = 0;
                const maxDepth = 8;
                
                while (parent && depth < maxDepth) {
                    // Look for container patterns
                    const isCardContainer = 
                        parent.classList.contains('team-member') ||
                        parent.classList.contains('member') ||
                        parent.classList.contains('card') ||
                        parent.classList.contains('person') ||
                        parent.classList.contains('profile') ||
                        parent.classList.contains('employee') ||
                        parent.classList.contains('dnd-column') ||
                        parent.tagName === 'ARTICLE' ||
                        parent.tagName === 'LI' ||
                        parent.tagName === 'SECTION';
                    
                    // Find name from headings nearby
                    if (!name || isIconOnly) {
                        const headings = parent.querySelectorAll('h1, h2, h3, h4, h5, h6, strong, .name, [class*="name"]');
                        for (const h of headings) {
                            const text = h.innerText?.trim();
                            if (text && text.length > 2 && text.length < 60) {
                                const lower = text.toLowerCase();
                                // Skip section headers
                                if (!lower.includes('team') && !lower.includes('about') && 
                                    !lower.includes('contact') && !lower.includes('our ') &&
                                    !lower.includes('leadership') && !lower.includes('meet')) {
                                    const words = text.split(/\\s+/);
                                    if (words.length >= 1 && words.length <= 5) {
                                        name = text;
                                        confidence = isIconOnly ? 0.9 : 0.8;  // Higher confidence for icon links
                                        break;
                                    }
                                }
                            }
                        }
                    }
                    
                    // Find role
                    if (!role) {
                        const roleSelectors = ['[class*="role"]', '[class*="title"]', '[class*="position"]', 
                                               '[class*="job"]', '[class*="functie"]', '.designation'];
                        for (const sel of roleSelectors) {
                            const roleEl = parent.querySelector(sel);
                            if (roleEl) {
                                const roleText = roleEl.innerText?.trim();
                                if (roleText && roleText.length > 2 && roleText.length < 100) {
                                    // Avoid grabbing the name as role
                                    if (name && roleText.includes(name)) continue;
                                    role = roleText.substring(0, 80);
                                    confidence = Math.max(confidence, 0.7);
                                    break;
                                }
                            }
                        }
                    }
                    
                    // Stop if we found enough or hit a card boundary
                    if ((name && role) || isCardContainer) break;
                    parent = parent.parentElement;
                    depth++;
                }
                
                // SNIPER Strategy 4: Page title fallback for individual profile pages
                if (!name && document.querySelectorAll('a[href*="linkedin.com/in/"], a[href*="linkedin.com/company/"], a[href*="linkedin.com/showcase/"]').length === 1) {
                    // Only one LinkedIn link on page = likely individual profile page
                    const pageTitle = document.querySelector('h1')?.innerText?.trim();
                    if (pageTitle && pageTitle.length < 60) {
                        const words = pageTitle.split(/\\s+/);
                        if (words.length >= 1 && words.length <= 5) {
                            name = pageTitle;
                            confidence = 0.85;
                        }
                    }
                }
                
                // SNIPER Strategy 5: Capture surrounding context for Python-side role extraction
                // Get text from the container for keyword-based role matching
                let contextText = '';
                let contextParent = link.parentElement;
                for (let i = 0; i < 6 && contextParent; i++) {
                    const text = contextParent.innerText || '';
                    if (text.length > contextText.length && text.length < 1000) {
                        contextText = text;
                    }
                    contextParent = contextParent.parentElement;
                }
                // Limit context text to reasonable size
                if (contextText.length > 500) {
                    contextText = contextText.substring(0, 500);
                }
                
                contacts.push({
                    url: href,
                    name: name,
                    role: role,
                    confidence: confidence,
                    contextText: contextText  // For Python-side role extraction
                });
            }
            
            return contacts;
        }
        """
        
        # =====================================================================
        # MAIN EXTRACTION LOGIC
        # =====================================================================
        
        # Initialize variables that need to be accessible in Layer 3
        profile_links = []
        visited_urls = {start_url}
        
        try:
            context = await self.browser.new_context(user_agent=USER_AGENT)
            page = await context.new_page()
            await page.route("**/*.{png,jpg,jpeg,gif,css,woff,woff2}", lambda route: route.abort())
            
            # Step 1: Check homepage
            logger.info(f"[Context] Checking homepage: {start_url}")
            try:
                await page.goto(start_url, wait_until="networkidle", timeout=TIMEOUT_SECONDS * 1000)
            except PlaywrightTimeoutError:
                logger.warning(f"Timeout on homepage, proceeding...")
            
            # Extract from homepage
            contacts = await page.evaluate(EXTRACT_CONTACTS_SNIPER_JS)
            for c in contacts:
                url = self._clean_url(c['url'])
                if url and url not in contacts_by_url:
                    # Use Python-side role extraction if JS didn't find a role
                    role = c.get('role')
                    context_text = c.get('contextText', '')
                    if not role and context_text:
                        role = extract_role_from_context(context_text)
                    
                    contacts_by_url[url] = PersonContact(
                        linkedin_url=url,
                        name=c.get('name'),
                        role=role,
                        confidence=c.get('confidence', 0),
                        source_page=start_url
                    )
            
            # Phase 3 (Vacuum) on homepage: Regex fallback
            homepage_html = await page.content()
            all_emails.update(RE_EMAIL.findall(homepage_html))
            for match in RE_LINKEDIN_URL.findall(homepage_html):
                url = self._clean_url(match)
                if url and any(x in url for x in ['/in/', '/company/', '/showcase/']) and url not in contacts_by_url:
                    contacts_by_url[url] = PersonContact(linkedin_url=url, source_page=start_url)
            
            # Step 2: Find and visit team pages and profile subpages
            logger.info(f"[Context] Finding team and profile pages...")
            
            # Get potential profile links from homepage
            profile_links = await page.evaluate(FIND_PROFILE_LINKS_JS)
            
            # Also add common team page patterns
            team_patterns = ['/team', '/our-team', '/people', '/our-people', '/about-us', 
                           '/about', '/over-ons', '/staff', '/leadership', '/professionals']
            for pattern in team_patterns:
                team_url = urllib.parse.urljoin(base, pattern)
                if not any(p['url'] == team_url for p in profile_links):
                    profile_links.append({'url': team_url, 'text': '', 'isProfilePage': True})
            
            # Visit profile pages (limit to prevent infinite crawling)
            pages_to_check = profile_links[:LEVEL2_LIMIT]
            
            for i, link_info in enumerate(pages_to_check):
                link_url = link_info['url']
                if link_url in visited_urls:
                    continue
                visited_urls.add(link_url)
                
                # Skip blocklisted URLs
                path_lower = urllib.parse.urlparse(link_url).path.lower()
                if any(b in path_lower for b in BLOCKLIST_PATTERNS):
                    continue
                
                try:
                    logger.info(f"  -> Checking: {link_url[:60]}...")
                    await page.goto(link_url, wait_until="domcontentloaded", timeout=TIMEOUT_SECONDS * 1000)
                    # Give JS time to render (for React/Next.js sites)
                    await asyncio.sleep(2)
                    
                    # Extract contacts with Sniper JS
                    page_contacts = await page.evaluate(EXTRACT_CONTACTS_SNIPER_JS)
                    for c in page_contacts:
                        url = self._clean_url(c['url'])
                        if url and url not in contacts_by_url:
                            # Use Python-side role extraction if JS didn't find a role
                            role = c.get('role')
                            context_text = c.get('contextText', '')
                            if not role and context_text:
                                role = extract_role_from_context(context_text)
                            
                            contacts_by_url[url] = PersonContact(
                                linkedin_url=url,
                                name=c.get('name'),
                                role=role,
                                confidence=c.get('confidence', 0),
                                source_page=link_url
                            )
                        elif url and url in contacts_by_url:
                            # Update with better data if available
                            existing = contacts_by_url[url]
                            if c.get('name') and (not existing.name or c.get('confidence', 0) > existing.confidence):
                                existing.name = c['name']
                                existing.confidence = c.get('confidence', 0)
                            if c.get('role') and not existing.role:
                                existing.role = c['role']
                            # Also try Python-side role extraction for upgrade
                            if not existing.role:
                                context_text = c.get('contextText', '')
                                if context_text:
                                    existing.role = extract_role_from_context(context_text)
                    
                    # Phase 3 (Vacuum): Regex fallback on page HTML
                    page_html = await page.content()
                    all_emails.update(RE_EMAIL.findall(page_html))
                    for match in RE_LINKEDIN_URL.findall(page_html):
                        url = self._clean_url(match)
                        if url and any(x in url for x in ['/in/', '/company/', '/showcase/']) and url not in contacts_by_url:
                            contacts_by_url[url] = PersonContact(linkedin_url=url, source_page=link_url)
                    
                    # Look for more profile links on this page (team pages)
                    if 'team' in path_lower or 'people' in path_lower or 'our-' in path_lower:
                        new_links = await page.evaluate(FIND_PROFILE_LINKS_JS)
                        for new_link in new_links:
                            if new_link['url'] not in visited_urls and len(pages_to_check) < LEVEL2_LIMIT + 20:
                                if any(a in new_link['url'].lower() for a in ALLOWLIST_PATTERNS):
                                    pages_to_check.append(new_link)
                    
                except PlaywrightTimeoutError:
                    logger.debug(f"Timeout on {link_url}")
                except Exception as e:
                    logger.debug(f"Error on {link_url}: {e}")
            
            logger.info(f"[Context] Found {len(contacts_by_url)} contacts from {len(visited_urls)} pages")
            
        except Exception as e:
            logger.warning(f"Phase 1-2 error: {e}")
        finally:
            if page:
                await page.close()
            if context:
                await context.close()
        
        # =====================================================================
        # LAYER 3 BOOST: Parallel BeautifulSoup batch checking
        # Fast static parsing as redundancy for Playwright failures
        # =====================================================================
        logger.info(f"[Context] Layer 3 (Structure): Parallel BeautifulSoup batch check...")
        
        try:
            async with aiohttp.ClientSession() as session:
                # Get all profile-like pages we discovered
                urls_to_check = [
                    p['url'] for p in profile_links[:50] 
                    if p['url'] not in visited_urls or len(contacts_by_url) < 5  # Re-check if we found few
                ]
                
                # Also add common team page patterns if we haven't found much
                if len(contacts_by_url) < 10:
                    for pattern in ['/team', '/our-team', '/people', '/our-people', '/about-us', '/staff']:
                        team_url = urllib.parse.urljoin(base, pattern)
                        if team_url not in urls_to_check:
                            urls_to_check.append(team_url)
                
                # Parallel fetch with BeautifulSoup
                tasks = [self._fetch_static_with_context(session, url) for url in urls_to_check[:30]]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                bs4_found = 0
                for bs4_result in results:
                    if isinstance(bs4_result, Exception):
                        continue
                    contacts_list, emails = bs4_result
                    all_emails.update(emails)
                    
                    for contact in contacts_list:
                        url = contact.linkedin_url
                        if url and url not in contacts_by_url:
                            contacts_by_url[url] = contact
                            bs4_found += 1
                        elif url and url in contacts_by_url:
                            # Update with context if better
                            existing = contacts_by_url[url]
                            if contact.name and (not existing.name or contact.confidence > existing.confidence):
                                existing.name = contact.name
                                existing.confidence = contact.confidence
                            if contact.role and not existing.role:
                                existing.role = contact.role
                
                if bs4_found > 0:
                    logger.info(f"[Context] Layer 3 found {bs4_found} additional contacts")
                    
        except Exception as e:
            logger.debug(f"Layer 3 batch error: {e}")
        
        # =====================================================================
        # PHASE 3: THE VACUUM - Also use original scraper as ultimate fallback
        # =====================================================================
        logger.info(f"[Context] Phase 3 (Vacuum): Running original scraper for backup...")
        
        try:
            original_urls, original_emails = await self.find_linkedin_links(start_url)
            all_emails.update(original_emails)
            
            # Add any URLs we missed
            for url in original_urls:
                if any(x in url for x in ['/in/', '/company/', '/showcase/']):
                    clean_url = self._clean_url(url)
                    if clean_url and clean_url not in contacts_by_url:
                        contacts_by_url[clean_url] = PersonContact(
                            linkedin_url=clean_url,
                            source_page=start_url
                        )
        except Exception as e:
            logger.warning(f"Vacuum fallback error: {e}")
        
        logger.info(f"[Context] Total contacts after vacuum: {len(contacts_by_url)}")
        
        # =====================================================================
        # STEP 4: Associate emails with contacts
        # =====================================================================
        personal_emails = [e for e in all_emails if not any(generic in e for generic in 
                          ['info@', 'contact@', 'office@', 'hello@', 'admin@', 'support@', 'kantoor@'])]
        generic_emails = [e for e in all_emails if e not in personal_emails]
        
        matched_emails = set()
        for email in personal_emails:
            email_local = email.split('@')[0].lower()
            email_local = re.sub(r'^(mr?s?|dr|prof|ing)\.?', '', email_local)
            
            best_match = None
            best_score = 0
            
            for url, contact in contacts_by_url.items():
                if contact.name and not contact.email:
                    name_parts = contact.name.lower().split()
                    match_score = sum(1 for part in name_parts if len(part) > 2 and part in email_local)
                    if match_score > best_score:
                        best_score = match_score
                        best_match = contact
            
            if best_match and best_score >= 1:
                best_match.email = email
                matched_emails.add(email)
        
        # Finalize results - deduplicate by normalizing URLs (remove trailing slashes)
        deduped_contacts: Dict[str, PersonContact] = {}
        for url, contact in contacts_by_url.items():
            norm_url = url.rstrip('/')
            if norm_url not in deduped_contacts:
                deduped_contacts[norm_url] = contact
            else:
                # Merge: prefer contact with more data (name/role)
                existing = deduped_contacts[norm_url]
                if contact.name and (not existing.name or contact.confidence > existing.confidence):
                    existing.name = contact.name
                    existing.confidence = contact.confidence
                if contact.role and not existing.role:
                    existing.role = contact.role
                if contact.email and not existing.email:
                    existing.email = contact.email
        
        result.contacts = list(deduped_contacts.values())
        result.orphan_emails = sorted((set(personal_emails) - matched_emails) | set(generic_emails))
        
        logger.info(f"[Context] Final: {len(result.contacts)} contacts, {sum(1 for c in result.contacts if c.name)} with names")
        
        return result
        


async def upload_to_google(session: aiohttp.ClientSession, website: str, links: List[str], emails: List[str], 
                           tab: str = None, row: int = None):
    """Sends the scraped data to the Google Apps Script Webhook (legacy format)."""
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


async def upload_to_google_doc(session: aiohttp.ClientSession, website: str, result: 'ScrapeResult',
                                webhook_url: str = None):
    """
    Sends enriched contact data to the Google Doc Apps Script.
    
    This version sends names, roles, and separates company LinkedIn from personal profiles.
    Compatible with the new table format that has Name columns.
    """
    if not result.contacts and not result.orphan_emails:
        logger.info(f"Skipping upload for {website}: No contacts or emails found.")
        return False
    
    # Use the provided webhook URL or fall back to default
    target_url = webhook_url or GOOGLE_DOC_WEBHOOK_URL
    if not target_url:
        logger.warning("No Google Doc webhook URL configured")
        return False
    
    # Separate company LinkedIn from personal profiles
    company_linkedin = ""
    personal_contacts = []
    
    for contact in result.contacts:
        if '/company/' in contact.linkedin_url or '/showcase/' in contact.linkedin_url:
            # First company URL found
            if not company_linkedin:
                company_linkedin = contact.linkedin_url
        else:
            # Personal profile
            personal_contacts.append({
                "url": contact.linkedin_url,
                "name": contact.name,
                "role": contact.role
            })
    
    # Build email list with any matched names
    email_list = []
    for contact in result.contacts:
        if contact.email:
            email_list.append({
                "email": contact.email,
                "name": contact.name,
                "role": contact.role
            })
    
    # Add orphan emails (no matched contact)
    for email in result.orphan_emails:
        email_list.append({
            "email": email,
            "name": None,
            "role": None
        })
    
    # Extract company name from URL
    try:
        domain = website.replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
        company_name = domain.split('.')[0].title()
    except:
        company_name = website
    
    # Build enriched payload
    payload = {
        "input_website": website,
        "company_name": company_name,
        "company_linkedin": company_linkedin,
        "contacts": personal_contacts,
        "emails": email_list
    }
    
    try:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        async with session.post(target_url, json=payload, ssl=ssl_context) as response:
            if response.status == 200:
                text = await response.text()
                if "success" in text.lower() or "ok" in text.lower():
                    logger.info(f"✅ Google Doc updated: {company_name} ({len(personal_contacts)} contacts, {len(email_list)} emails)")
                    return True
                else:
                    logger.warning(f"⚠️ Google Doc response: {text}")
                    return False
            else:
                logger.error(f"❌ Google Doc upload failed: {response.status}")
                return False
    except Exception as e:
        logger.error(f"❌ Google Doc upload error: {e}")
        return False



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


async def main_with_context(urls: List[str]):
    """
    Enhanced main function that extracts LinkedIn contacts with name, role, and email context.
    Outputs results as JSON.
    """
    import time
    start_time = time.time()
    
    logger.info(f"🔥 Starting CONTEXT-AWARE batch processing of {len(urls)} sites")
    logger.info(f"   This mode extracts names, roles, and emails for each LinkedIn profile.")
    
    finder = LinkedInFinder()
    await finder.start()
    
    all_results = []
    
    try:
        for i, url in enumerate(urls, 1):
            logger.info(f"\n[{i}/{len(urls)}] Processing: {url}")
            try:
                result = await finder.find_contacts_with_context(url)
                all_results.append(result.to_dict())
                
                # Log summary for this site
                contacts_with_names = sum(1 for c in result.contacts if c.name)
                contacts_with_roles = sum(1 for c in result.contacts if c.role)
                contacts_with_emails = sum(1 for c in result.contacts if c.email)
                
                logger.info(f"   ✅ Found {len(result.contacts)} contacts:")
                logger.info(f"      - With names: {contacts_with_names}")
                logger.info(f"      - With roles: {contacts_with_roles}")
                logger.info(f"      - With emails: {contacts_with_emails}")
                logger.info(f"   📧 Orphan emails: {len(result.orphan_emails)}")
                
                # Print first few contacts for verification
                for c in result.contacts[:3]:
                    name = c.name or "Unknown"
                    role = c.role or "No role"
                    email = c.email or "No email"
                    logger.info(f"      • {name} | {role} | {email}")
                if len(result.contacts) > 3:
                    logger.info(f"      ... and {len(result.contacts) - 3} more")
                    
            except Exception as e:
                logger.error(f"   ❌ Error processing {url}: {e}")
                all_results.append({
                    "website": url,
                    "error": str(e),
                    "contacts": [],
                    "orphan_emails": []
                })
    finally:
        await finder.stop()
    
    # Save results to JSON
    output_file = "linkedin_contacts.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    
    elapsed = time.time() - start_time
    total_contacts = sum(len(r.get("contacts", [])) for r in all_results)
    total_with_names = sum(1 for r in all_results for c in r.get("contacts", []) if c.get("name"))
    total_with_roles = sum(1 for r in all_results for c in r.get("contacts", []) if c.get("role"))
    total_emails = sum(len(r.get("orphan_emails", [])) for r in all_results)
    
    logger.info(f"\n")
    logger.info(f"{'='*60}")
    logger.info(f"📊 CONTEXT-AWARE SCRAPING COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"   Sites processed: {len(urls)}")
    logger.info(f"   Total contacts: {total_contacts}")
    logger.info(f"   With names: {total_with_names}")
    logger.info(f"   With roles: {total_with_roles}")
    logger.info(f"   Orphan emails: {total_emails}")
    logger.info(f"   Time elapsed: {elapsed:.1f}s ({elapsed/len(urls):.1f}s per site)")
    logger.info(f"{'='*60}")
    logger.info(f"Results saved to {output_file}")
    
    return all_results


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--sheet":
        # Fetch from Google Sheets
        batch_size = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        asyncio.run(main_from_sheet(batch_size))
    elif len(sys.argv) > 1 and sys.argv[1] == "--context":
        # Context-aware mode with name/role extraction
        test_urls = [
            "https://www.assetpartners.nl/",
            "https://www.famadvocaten.nl/nl/",
            "https://www.axento.nl/"
        ]
        asyncio.run(main_with_context(test_urls))
    else:
        # Default: manual test URLs (legacy mode)
        test_urls = [
            "https://www.assetpartners.nl/",
            "https://www.famadvocaten.nl/nl/",
            "https://www.axento.nl/"
        ]
        asyncio.run(main(test_urls))
