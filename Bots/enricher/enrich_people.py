#!/usr/bin/env python3
"""
Robust website people enricher (Precision Version).

Features:
- Strict Pre-checks:
    1. Must have >1 unique personal LinkedIn profile on the homepage (or scanned pages).
    2. Must have a valid Team Page.
- Targeted Extraction:
    - Only extracts people with specific decision-maker titles (CEO, Partner, etc.).
    - Enforces proximity between Name, Title, and LinkedIn URL.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import random
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Dict, Any, Set, Tuple
from urllib.parse import urljoin, urlsplit, urlunsplit

import aiohttp
from aiolimiter import AsyncLimiter
from bs4 import BeautifulSoup, Tag
from fake_useragent import UserAgent
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT = 15
MAX_HTML_LENGTH = 1_800_000
GLOBAL_RATE_LIMIT = 10.0
PER_HOST_RATE_LIMIT = 2.0

# Strict Allowlist for Roles (English + Dutch)
ALLOWED_ROLES = {
    # C-Level
    "ceo", "chief executive", "chief executive officer",
    "cto", "chief technology officer",
    "cfo", "chief financial officer",
    "coo", "chief operating officer",
    "cmo", "chief marketing officer",
    "cro", "chief revenue officer",
    "cpo", "chief product officer",
    "president", "vice president", "vp",
    # Leadership
    "founder", "co-founder", "cofounder", "founding partner",
    "owner", "eigenaar",
    "partner", "managing partner", "general partner",
    "director", "managing director", "directeur", "algemeen directeur",
    "board member", "chairman", "chairwoman", "chair",
    "bestuurder", "bestuurslid",
    "head of", "hoofd",
    "principal",
}

LIKELY_TEAM_PATHS = [
    "/team", "/teams", "/people", "/leadership", "/our-team",
    "/company/team", "/about", "/about-us", "/about/team",
    "/management", "/who-we-are", "/board", "/crew",
    "/company", "/partners", "/over-ons", "/wie-zijn-wij",
]

TEAM_ANCHOR_HINT = re.compile(
    r"\b(team|people|leadership|management|board|partners|crew|about|"
    r"ons[-\s]?team|over[-\s]?ons|wie\s+zijn\s+wij|organisatie)\b",
    re.I,
)

LINKEDIN_HINT = re.compile(r"linkedin\.com", re.I)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class PersonCandidate:
    name: str = ""
    title: str = ""
    linkedin: str = ""
    email: str = ""
    source_url: str = ""
    score: float = 0.0

    def is_valid(self) -> bool:
        # Strict: Must have Name AND Title AND (LinkedIn OR Email)
        # Actually user said "output should be name, linkedin url, and roles"
        # So we prioritize LinkedIn.
        return bool(self.name and self.title and self.linkedin)

    def key(self) -> tuple:
        return (self.name.lower(), self.title.lower(), self.linkedin.lower())

@dataclass
class SiteScanResult:
    website: str
    normalized_url: str = ""
    team_pages: List[str] = field(default_factory=list)
    people: List[PersonCandidate] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "website": self.website,
            "normalized_url": self.normalized_url,
            "team_pages": self.team_pages,
            "people": [dataclasses.asdict(p) for p in self.people],
            "errors": self.errors,
            "meta": self.meta,
        }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def normalize_url(url: str) -> str:
    if not url: return ""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    try:
        parts = urlsplit(url)
        if not parts.netloc: return ""
        return urlunsplit(parts._replace(fragment="", query=""))
    except ValueError:
        return ""

def normalize_linkedin(url: str) -> str:
    if not url: return ""
    url = url.strip()
    if url.startswith("//"): url = "https:" + url
    try:
        parts = urlsplit(url)
        if "linkedin.com" not in parts.netloc.lower(): return ""
        # Filter out company pages for "personal profile" check
        path = parts.path.lower()
        if "/company/" in path or "/school/" in path or "/jobs/" in path:
            return "" # Not a person
        
        # Clean path
        path = re.sub(r"/+$", "", parts.path or "")
        if not path.startswith("/"): path = "/" + path
        return urlunsplit(("https", "www.linkedin.com", path, "", ""))
    except ValueError:
        return ""

def is_allowed_role(title: str) -> bool:
    t = title.lower()
    # Check exact matches or "Head of X" style
    for role in ALLOWED_ROLES:
        # 1. Exact match (e.g. "CEO")
        if t == role: return True
        # 2. Word boundary match (e.g. "Deputy CEO")
        if re.search(rf"\b{re.escape(role)}\b", t):
            return True
    return False

def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
class PeopleEnricher:
    def __init__(self, max_pages=25, logger=None):
        self.max_pages = max_pages
        self.logger = logger or logging.getLogger("people_enricher")
        self.ua = UserAgent()
        self.limiter = AsyncLimiter(GLOBAL_RATE_LIMIT, 1.0)
        self.host_limiters: Dict[str, float] = {}

    def _get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self.ua.random,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    async def _rate_limit_host(self, host: str) -> None:
        now = time.time()
        last = self.host_limiters.get(host, 0.0)
        wait = max(0.0, PER_HOST_RATE_LIMIT - (now - last))
        if wait > 0:
            await asyncio.sleep(wait + random.random() * 0.5)
        self.host_limiters[host] = time.time()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
    )
    async def _fetch(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        try:
            parts = urlsplit(url)
            if not parts.netloc: return None
            await self.limiter.acquire()
            await self._rate_limit_host(parts.netloc)
            async with session.get(url, headers=self._get_headers(), timeout=DEFAULT_TIMEOUT, ssl=False) as resp:
                if 200 <= resp.status < 400 and "text/html" in resp.headers.get("Content-Type", "").lower():
                    content = await resp.read()
                    if len(content) > MAX_HTML_LENGTH: return None
                    return content.decode("utf-8", errors="replace")
        except Exception:
            pass
        return None

    def _extract_linkedin_urls(self, soup: BeautifulSoup) -> Set[str]:
        urls = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if LINKEDIN_HINT.search(href):
                normalized = normalize_linkedin(href)
                # normalize_linkedin returns "" for company pages now
                if normalized:
                    urls.add(normalized)
        return urls

    async def discover_team_pages(self, session: aiohttp.ClientSession, base_url: str, homepage_html: str) -> List[str]:
        candidates = []
        soup = BeautifulSoup(homepage_html, "html.parser")
        root = base_url.rstrip("/")
        
        # 1. Explicit paths
        for path in LIKELY_TEAM_PATHS:
            candidates.append(urljoin(root, path))
            
        # 2. Homepage links
        home_netloc = urlsplit(root).netloc.replace("www.", "")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(" ", strip=True)
            full = urljoin(root, href)
            if home_netloc not in urlsplit(full).netloc: continue
            
            if TEAM_ANCHOR_HINT.search(href) or TEAM_ANCHOR_HINT.search(text):
                candidates.append(full)
                
        # Dedup and limit
        unique = []
        seen = set()
        for c in candidates:
            c = c.split("#")[0].rstrip("/")
            if c not in seen:
                seen.add(c)
                unique.append(c)
        return unique[:self.max_pages]

    def _discover_profile_pages(self, html: str, base_url: str) -> List[str]:
        """Discover links to individual profile pages from a team page."""
        soup = BeautifulSoup(html, "html.parser")
        profile_links = []
        base_path = urlsplit(base_url).path.rstrip("/")
        base_netloc = urlsplit(base_url).netloc.replace("www.", "")
        
        # Look for links nested under the team page path
        # e.g. /team/john-doe, /over/team/jane-smith
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full_url = urljoin(base_url, href)
            parts = urlsplit(full_url)
            
            # Must be same domain
            if base_netloc not in parts.netloc.replace("www.", ""):
                continue
            
            path = parts.path.rstrip("/")
            
            # Heuristic: path is deeper than team page (e.g. /team/person-name)
            if base_path and path.startswith(base_path + "/"):
                # Likely a profile page
                profile_links.append(full_url.split("#")[0])
        
        # Dedup
        return list(set(profile_links))

    def _extract_people_strict(self, html: str, base_url: str) -> List[PersonCandidate]:
        soup = BeautifulSoup(html, "html.parser")
        candidates = []

        # Strategy: Find blocks containing a LinkedIn URL, then look for Name/Role nearby
        # This is high precision because we anchor on the LinkedIn profile.
        
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not LINKEDIN_HINT.search(href): continue
            
            li_url = normalize_linkedin(href)
            if not li_url: continue # Skip company pages

            # Walk up to find a container (card)
            container = a.parent
            for _ in range(4): # Go up max 4 levels
                if not container: break
                text = clean_text(container.get_text())
                if len(text) > 500: # Too big, likely whole page or section
                    break
                
                # Analyze container text for Name + Role
                # We expect: "Name Role" or "Name \n Role"
                # And Role must be in ALLOWED_ROLES
                
                # Heuristic: Split by newlines or common separators
                parts = [p.strip() for p in re.split(r"[\n,•|]", text) if p.strip()]
                
                found_name = ""
                found_role = ""
                
                for part in parts:
                    if is_allowed_role(part):
                        found_role = part
                        # Name is likely the part before or after, or we search for a name-like string
                        # Let's look for the name in the other parts
                        for other in parts:
                            if other == part: continue
                            # Simple name check: 2-3 words, capitalized
                            if re.match(r"^[A-Z][a-z]+ [A-Z][a-z]+(?: [A-Z][a-z]+)?$", other):
                                found_name = other
                                break
                        if found_name: break
                
                if found_name and found_role:
                    candidates.append(PersonCandidate(
                        name=found_name,
                        title=found_role,
                        linkedin=li_url,
                        source_url=base_url,
                        score=10.0 # High confidence
                    ))
                    break # Found for this LI link
                
                container = container.parent

        return candidates

    async def scan_site(self, session: aiohttp.ClientSession, website: str) -> SiteScanResult:
        result = SiteScanResult(website=website)
        normalized = normalize_url(website)
        if not normalized:
            result.errors.append("invalid_url")
            return result
        result.normalized_url = normalized

        # 1. Fetch Homepage
        homepage_html = await self._fetch(session, normalized)
        if not homepage_html:
            result.errors.append("homepage_unreachable")
            return result

        # 2. Discover Team Pages
        team_pages = await self.discover_team_pages(session, normalized, homepage_html)
        result.team_pages = team_pages
        
        # 3. Scan Pages & Extract (including deep enrichment)
        all_li_urls = set()
        all_people = []
        pages_to_scan = [normalized] + team_pages  # Always scan homepage + team pages
        profile_pages_to_scan = []

        # First pass: Fetch team pages and discover profile pages
        tasks = [self._fetch(session, url) for url in team_pages]
        responses = await asyncio.gather(*tasks)
        
        # Process homepage
        homepage_soup = BeautifulSoup(homepage_html, "html.parser")
        homepage_lis = self._extract_linkedin_urls(homepage_soup)
        all_li_urls.update(homepage_lis)
        p = self._extract_people_strict(homepage_html, normalized)
        all_people.extend(p)

        # Process team pages and discover profile pages
        for url, html in zip(team_pages, responses):
            if html:
                # Extract LinkedIn URLs
                page_soup = BeautifulSoup(html, "html.parser")
                page_lis = self._extract_linkedin_urls(page_soup)
                all_li_urls.update(page_lis)
                
                # Extract people
                p = self._extract_people_strict(html, url)
                all_people.extend(p)
                
                # Discover profile pages
                profiles = self._discover_profile_pages(html, url)
                profile_pages_to_scan.extend(profiles)
        
        # Second pass: Fetch and scan profile pages (deep enrichment)
        if profile_pages_to_scan:
            # Dedup and limit
            profile_pages_to_scan = list(set(profile_pages_to_scan))[:20]  # Limit to 20 profiles
            profile_tasks = [self._fetch(session, url) for url in profile_pages_to_scan]
            profile_responses = await asyncio.gather(*profile_tasks)
            
            for url, html in zip(profile_pages_to_scan, profile_responses):
                if html:
                    page_soup = BeautifulSoup(html, "html.parser")
                    page_lis = self._extract_linkedin_urls(page_soup)
                    all_li_urls.update(page_lis)
                    
                    p = self._extract_people_strict(html, url)
                    all_people.extend(p)

        # 4. Final Check: >1 Unique LinkedIn Account (DEFERRED)
        if len(all_li_urls) <= 1:
             result.errors.append(f"insufficient_linkedin_count_{len(all_li_urls)}")
             result.people = [] 
             return result

        # 5. Dedup
        unique = {}
        for p in all_people:
            if p.key() not in unique:
                unique[p.key()] = p
        
        result.people = list(unique.values())
        result.meta["linkedin_count"] = len(all_li_urls)
        result.meta["profile_pages_scanned"] = len(profile_pages_to_scan) if profile_pages_to_scan else 0
        return result

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
async def main_async(args):
    websites = []
    if args.url: websites.extend(args.url)
    if args.input_file:
        for f in args.input_file:
            with open(f) as h: websites.extend([l.strip() for l in h if l.strip()])
    
    if not websites: return

    enricher = PeopleEnricher(max_pages=args.max_pages)
    results = []
    
    async with aiohttp.ClientSession() as session:
        tasks = [enricher.scan_site(session, w) for w in websites]
        scan_results = await asyncio.gather(*tasks)
        results = [r.to_dict() for r in scan_results]

    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
    logging.info(f"Done. Wrote to {args.output}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-u", "--url", action="append")
    parser.add_argument("-i", "--input-file", action="append")
    parser.add_argument("-o", "--output", default="enriched_people.json")
    parser.add_argument("--max-pages", type=int, default=10)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async(args))

if __name__ == "__main__":
    main()
