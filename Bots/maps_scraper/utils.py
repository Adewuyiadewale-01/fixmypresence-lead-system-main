"""Utility helpers for the Google Maps lead scraper."""

from __future__ import annotations

import csv
import hashlib
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Set
from urllib.parse import parse_qs, urlparse

import tldextract

from config import AGGREGATOR_DOMAINS


def generate_lead_id(name: str, website: Optional[str]) -> str:
    """Generate a deterministic unique ID for a lead.
    
    Uses MD5 hash of normalized domain + normalized business name.
    This allows cross-source deduplication - same business from 
    different scrapers will get the same ID.
    """
    # Normalize business name
    name_normalized = name.strip().lower() if name else ""
    
    # Get normalized domain, fall back to empty string
    domain = normalize_domain(website) if website else ""
    
    # Create composite key: domain takes priority, name as secondary identifier
    if domain:
        composite_key = f"{domain}|{name_normalized}"
    else:
        composite_key = f"nosite|{name_normalized}"
    
    # Generate short hash (first 12 chars of MD5 = 48 bits = ~281 trillion combinations)
    return hashlib.md5(composite_key.encode('utf-8')).hexdigest()[:12]


@dataclass(frozen=True)
class Lead:
    """Canonical representation of a scraped lead."""

    name: str
    website: Optional[str]
    location: str
    keyword: str
    place_url: Optional[str]
    status: str = ""

    @property
    def lead_id(self) -> str:
        """Deterministic unique ID for cross-source deduplication."""
        return generate_lead_id(self.name, self.website)

    @property
    def domain(self) -> Optional[str]:
        if not self.website:
            return None
        return normalize_domain(self.website)

    @property
    def place_key(self) -> Optional[str]:
        if not self.place_url:
            return None
        return extract_place_key(self.place_url)


def normalize_domain(url: str) -> Optional[str]:
    """Normalize a URL down to its registered domain."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        if parsed.hostname:
            host = parsed.hostname.lower()
        else:
            host = url.lower()
    except Exception:
        host = url.lower()

    if host.startswith("www."):
        host = host[4:]

    try:
        extracted = tldextract.extract(host)
        if extracted.registered_domain:
            return extracted.registered_domain.lower()
    except Exception:
        pass

    return host or None


def extract_place_key(place_url: Optional[str]) -> Optional[str]:
    """Extract a stable Google Maps place identifier from a place URL."""
    if not place_url:
        return None

    raw = place_url.strip()
    if not raw:
        return None

    try:
        parsed = urlparse(raw)
    except Exception:
        parsed = None

    query = parse_qs(parsed.query) if parsed else {}
    for param in ("cid", "ftid"):
        values = query.get(param)
        if values and values[0]:
            return f"{param}:{values[0].strip().lower()}"

    match = re.search(r"!1s([^!/?&]+)", raw)
    if match:
        return f"1s:{match.group(1).strip().lower()}"

    if parsed and parsed.path:
        path = parsed.path.rstrip("/")
        if "/maps/place/" in path:
            return f"path:{path.lower()}"

    return raw.lower()


def extract_strong_place_key(place_url: Optional[str]) -> Optional[str]:
    """Extract only Google identifiers safe enough for pre-extraction dedupe."""
    if not place_url:
        return None

    raw = place_url.strip()
    if not raw:
        return None

    try:
        parsed = urlparse(raw)
        query = parse_qs(parsed.query)
    except Exception:
        query = {}

    for param in ("cid", "ftid"):
        values = query.get(param)
        if values and values[0]:
            return f"{param}:{values[0].strip().lower()}"

    match = re.search(r"!1s([^!/?&]+)", raw)
    if match:
        return f"1s:{match.group(1).strip().lower()}"

    return None


def is_aggregator(url: Optional[str]) -> bool:
    if not url:
        return False
    domain = normalize_domain(url)
    if not domain:
        return False
    return any(domain == agg or domain.endswith(f".{agg}") for agg in AGGREGATOR_DOMAINS)


def dedupe_leads(
    leads: Iterable[Lead],
    seen_places: Set[str],
    seen_domains: Set[str],
    seen_names: Set[str],
):
    """Yield leads that are new by place identity, then domain, then name+location."""
    for lead in leads:
        place_key = lead.place_key
        domain = lead.domain
        name_key = f"{lead.name.strip().lower()}|{lead.location.strip().lower()}"

        if place_key:
            if place_key in seen_places:
                continue
            seen_places.add(place_key)

        if domain:
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            yield lead
            continue

        if name_key in seen_names:
            continue
        seen_names.add(name_key)
        yield lead


def export_batch(batch: Iterable[Lead], destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["lead_id", "name", "website", "location", "keyword", "place_url", "status"])
        for lead in batch:
            writer.writerow([
                lead.lead_id,
                lead.name,
                lead.website or "",
                lead.location,
                lead.keyword,
                lead.place_url or "",
                lead.status,
            ])


def append_to_master(master: Path, batch: Iterable[Lead]):
    master.parent.mkdir(parents=True, exist_ok=True)
    write_header = not master.exists() or master.stat().st_size == 0
    with master.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(["lead_id", "name", "website", "location", "keyword", "place_url", "status"])
        for lead in batch:
            writer.writerow([
                lead.lead_id,
                lead.name,
                lead.website or "",
                lead.location,
                lead.keyword,
                lead.place_url or "",
                lead.status,
            ])


def human_throttle(min_seconds: float, max_seconds: float):
    time.sleep(random.uniform(min_seconds, max_seconds))
