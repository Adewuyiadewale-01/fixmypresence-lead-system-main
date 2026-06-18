"""High-reliability Google Maps scraping primitives."""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote_plus

from playwright.sync_api import Playwright, TimeoutError as PlaywrightTimeoutError, sync_playwright
from tenacity import RetryError, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import (
    CARD_CLICK_TIMEOUT_MS,
    CAPTCHA_BACKOFF_S,
    MAPS_ADAPTIVE_SIDEBAR_WAIT_ENABLED,
    MAPS_BATCH_CARD_READ_ENABLED,
    MAPS_TELEMETRY_ENABLED,
    MAX_DELAY_S,
    MIN_DELAY_S,
    PLAYWRIGHT_USER_DATA,
    SIDEBAR_ADAPTIVE_MAX_WAIT_MS,
    SCROLL_WAIT_MS,
    SIDEBAR_AFTER_CLICK_WAIT_MS,
)
from utils import Lead, extract_strong_place_key, human_throttle, is_aggregator


class CaptchaDetected(RuntimeError):
    ...


@dataclass
class SearchTelemetry:
    keyword: str = ""
    location: str = ""
    search_duration_ms: float = 0.0
    cards_seen: int = 0
    leads_returned: int = 0
    leads_with_website: int = 0
    batch_read_complete: int = 0
    fallback_to_existing_extract_card: int = 0
    cards_completed_without_sidebar: int = 0
    sidebar_click_attempts: int = 0
    sidebar_click_successes: int = 0
    sidebar_extraction_failures: int = 0
    card_extract_duration_ms: float = 0.0
    sidebar_wait_duration_ms: float = 0.0
    identity_cards_submitted: int = 0
    cards_with_strong_place_key: int = 0
    early_dedupe_approved: int = 0
    early_duplicates_skipped: int = 0
    historical_duplicates_skipped: int = 0
    inflight_duplicates_skipped: int = 0
    same_batch_duplicates_skipped: int = 0
    unidentified_cards_extracted: int = 0
    coordinator_requests: int = 0
    coordinator_lookup_duration_ms: float = 0.0
    coordinator_max_lookup_duration_ms: float = 0.0
    coordinator_timeouts: int = 0
    coordinator_errors: int = 0
    reservation_commits: int = 0
    reservation_releases: int = 0
    reservation_expirations: int = 0
    sidebar_fallbacks_avoided_estimate: int = 0
    feature_flags: dict = field(default_factory=dict)

    @staticmethod
    def _pct(part: int, whole: int):
        return round((part / whole) * 100, 1) if whole else None

    def as_dict(self) -> dict:
        return {
            "keyword": self.keyword,
            "location": self.location,
            "search_duration_ms": round(self.search_duration_ms, 1),
            "cards_seen": self.cards_seen,
            "leads_returned": self.leads_returned,
            "leads_with_website": self.leads_with_website,
            "website_found_rate": self._pct(self.leads_with_website, self.leads_returned),
            "avg_time_per_lead_ms": round(self.search_duration_ms / self.leads_returned, 1) if self.leads_returned else None,
            "avg_time_per_website_found_lead_ms": (
                round(self.search_duration_ms / self.leads_with_website, 1)
                if self.leads_with_website
                else None
            ),
            "batch_read_complete": self.batch_read_complete,
            "fallback_to_existing_extract_card": self.fallback_to_existing_extract_card,
            "cards_completed_without_sidebar": self.cards_completed_without_sidebar,
            "sidebar_click_attempts": self.sidebar_click_attempts,
            "sidebar_click_successes": self.sidebar_click_successes,
            "sidebar_extraction_failures": self.sidebar_extraction_failures,
            "card_extract_duration_ms": round(self.card_extract_duration_ms, 1),
            "sidebar_wait_duration_ms": round(self.sidebar_wait_duration_ms, 1),
            "identity_cards_submitted": self.identity_cards_submitted,
            "cards_with_strong_place_key": self.cards_with_strong_place_key,
            "early_dedupe_approved": self.early_dedupe_approved,
            "early_duplicates_skipped": self.early_duplicates_skipped,
            "historical_duplicates_skipped": self.historical_duplicates_skipped,
            "inflight_duplicates_skipped": self.inflight_duplicates_skipped,
            "same_batch_duplicates_skipped": self.same_batch_duplicates_skipped,
            "unidentified_cards_extracted": self.unidentified_cards_extracted,
            "coordinator_requests": self.coordinator_requests,
            "coordinator_lookup_duration_ms": round(self.coordinator_lookup_duration_ms, 3),
            "coordinator_max_lookup_duration_ms": round(self.coordinator_max_lookup_duration_ms, 3),
            "coordinator_timeouts": self.coordinator_timeouts,
            "coordinator_errors": self.coordinator_errors,
            "reservation_commits": self.reservation_commits,
            "reservation_releases": self.reservation_releases,
            "reservation_expirations": self.reservation_expirations,
            "sidebar_fallbacks_avoided_estimate": self.sidebar_fallbacks_avoided_estimate,
            "batch_read_complete_rate": self._pct(self.batch_read_complete, self.cards_seen),
            "fallback_rate": self._pct(self.fallback_to_existing_extract_card, self.cards_seen),
            "sidebar_click_rate": self._pct(self.sidebar_click_attempts, self.cards_seen),
            "sidebar_success_rate": self._pct(self.sidebar_click_successes, self.sidebar_click_attempts),
            "sidebar_failure_rate": self._pct(self.sidebar_extraction_failures, self.sidebar_click_attempts),
            "no_sidebar_completion_rate": self._pct(self.cards_completed_without_sidebar, self.cards_seen),
            "strong_place_key_coverage": self._pct(self.cards_with_strong_place_key, self.identity_cards_submitted),
            "early_duplicate_rate": self._pct(self.early_duplicates_skipped, self.cards_with_strong_place_key),
            "historical_duplicate_rate": self._pct(self.historical_duplicates_skipped, self.cards_with_strong_place_key),
            "inflight_duplicate_rate": self._pct(self.inflight_duplicates_skipped, self.cards_with_strong_place_key),
            "extraction_avoidance_rate": self._pct(self.early_duplicates_skipped, self.identity_cards_submitted),
            "average_coordinator_latency_ms": (
                round(self.coordinator_lookup_duration_ms / self.coordinator_requests, 3)
                if self.coordinator_requests
                else None
            ),
            "coordinator_fail_open_rate": self._pct(
                self.coordinator_timeouts + self.coordinator_errors,
                self.coordinator_requests,
            ),
            "feature_flags": dict(self.feature_flags),
        }


class GoogleMapsScraper:
    """Encapsulates the browser automation for scraping Google Maps listings."""

    def __init__(self, headless: bool = False, user_data_dir: str = None):
        self.headless = headless
        self._user_data_dir = user_data_dir or str(PLAYWRIGHT_USER_DATA)
        self._play: Optional[Playwright] = None
        self._browser = None
        self._context = None
        self._page = None
        self.last_telemetry = SearchTelemetry().as_dict()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        if self._page:
            return
        self._play = sync_playwright().start()
        self._browser = self._play.chromium.launch_persistent_context(
            user_data_dir=self._user_data_dir,
            headless=self.headless,
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
            locale="en-US",
        )
        self._context = self._browser
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

    def stop(self):
        with contextlib.suppress(Exception, KeyboardInterrupt):
            if self._context:
                self._context.close()
        with contextlib.suppress(Exception, KeyboardInterrupt):
            if self._play:
                self._play.stop()
        self._play = None
        self._browser = None
        self._context = None
        self._page = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        keyword: str,
        location: str,
        identity_gate: Optional[Callable[[List[Dict[str, Any]]], Dict[str, Any]]] = None,
        reservation_outcome: Optional[Callable[[str, bool], None]] = None,
    ) -> List[Lead]:
        if not self._page:
            raise RuntimeError("Call start() before search().")

        started = time.perf_counter()
        telemetry = SearchTelemetry(
            keyword=keyword,
            location=location,
            feature_flags={
                "telemetry": MAPS_TELEMETRY_ENABLED,
                "batch_card_read": MAPS_BATCH_CARD_READ_ENABLED,
                "adaptive_sidebar_wait": MAPS_ADAPTIVE_SIDEBAR_WAIT_ENABLED,
                "early_dedupe": identity_gate is not None,
            },
        )
        query = f"{keyword} in {location}"
        url = f"https://www.google.com/maps/search/{quote_plus(query)}?hl=en&gl=nl"

        try:
            has_results = self._attempt_navigation(url)
        except RetryError as exc:
            raise RuntimeError(f"Navigation failed for query '{query}': {exc}") from exc
        
        # Early return if no results found for this query
        if not has_results:
            self._finish_telemetry(telemetry, [], started)
            return []

        leads: List[Lead] = []
        processed = 0
        stable_rounds = 0

        while True:
            self._check_captcha()

            cards = self._cards_locator()
            count = cards.count()
            if count == 0:
                break

            batch_cards = self._read_visible_card_data(cards) if MAPS_BATCH_CARD_READ_ENABLED else []
            gate_decisions: Dict[int, Dict[str, Any]] = {}
            if identity_gate and count > processed:
                candidates = []
                for i in range(processed, count):
                    data = batch_cards[i] if i < len(batch_cards) else {}
                    place_key = extract_strong_place_key(str(data.get("place_url") or ""))
                    candidates.append({
                        "candidate_index": i,
                        "place_key": place_key,
                        "visible_website": bool(str(data.get("website") or "").strip()),
                    })
                telemetry.identity_cards_submitted += len(candidates)
                telemetry.cards_with_strong_place_key += sum(1 for item in candidates if item["place_key"])
                telemetry.coordinator_requests += 1
                try:
                    gate_result = identity_gate(candidates) or {}
                    lookup_ms = float(gate_result.get("lookup_duration_ms") or 0.0)
                    telemetry.coordinator_lookup_duration_ms += lookup_ms
                    telemetry.coordinator_max_lookup_duration_ms = max(
                        telemetry.coordinator_max_lookup_duration_ms,
                        lookup_ms,
                    )
                    telemetry.coordinator_timeouts += int(bool(gate_result.get("timeout")))
                    telemetry.coordinator_errors += int(bool(gate_result.get("error")))
                    telemetry.reservation_expirations += int(gate_result.get("reservation_expirations") or 0)
                    for item in gate_result.get("decisions") or []:
                        if isinstance(item, dict):
                            gate_decisions[int(item.get("candidate_index", -1))] = item
                except Exception:
                    telemetry.coordinator_errors += 1

            for i in range(processed, count):
                card = cards.nth(i)
                telemetry.cards_seen += 1
                card_started = time.perf_counter()
                batch_candidate = batch_cards[i] if i < len(batch_cards) else {}
                gate_decision = gate_decisions.get(i) or {}
                decision = gate_decision.get("decision")
                reservation_token = str(gate_decision.get("reservation_token") or "")
                if decision in {"SKIP_HISTORICAL", "SKIP_IN_FLIGHT", "SKIP_SAME_BATCH"}:
                    telemetry.early_duplicates_skipped += 1
                    if decision == "SKIP_HISTORICAL":
                        telemetry.historical_duplicates_skipped += 1
                    elif decision == "SKIP_SAME_BATCH":
                        telemetry.same_batch_duplicates_skipped += 1
                    else:
                        telemetry.inflight_duplicates_skipped += 1
                    if not str(batch_candidate.get("website") or "").strip():
                        telemetry.sidebar_fallbacks_avoided_estimate += 1
                    telemetry.card_extract_duration_ms += (time.perf_counter() - card_started) * 1000
                    human_throttle(MIN_DELAY_S, MAX_DELAY_S)
                    continue
                if decision == "EXTRACT_NEW":
                    telemetry.early_dedupe_approved += 1
                elif identity_gate and not extract_strong_place_key(str(batch_candidate.get("place_url") or "")):
                    telemetry.unidentified_cards_extracted += 1

                lead = self._lead_from_batch_card(batch_candidate, keyword, location)
                if lead:
                    telemetry.batch_read_complete += 1
                    telemetry.cards_completed_without_sidebar += 1
                else:
                    telemetry.fallback_to_existing_extract_card += 1
                    before_attempts = telemetry.sidebar_click_attempts
                    lead = self._extract_card(card, keyword, location, telemetry=telemetry)
                    if telemetry.sidebar_click_attempts == before_attempts:
                        telemetry.cards_completed_without_sidebar += 1
                telemetry.card_extract_duration_ms += (time.perf_counter() - card_started) * 1000
                if lead:
                    leads.append(lead)
                if reservation_token and reservation_outcome:
                    try:
                        reservation_outcome(reservation_token, lead is not None)
                        if lead is not None:
                            telemetry.reservation_commits += 1
                        else:
                            telemetry.reservation_releases += 1
                    except Exception:
                        telemetry.coordinator_errors += 1
                human_throttle(MIN_DELAY_S, MAX_DELAY_S)

            if count == processed:
                stable_rounds += 1
            else:
                stable_rounds = 0
            processed = count

            if stable_rounds >= 3:
                break

            self._scroll_results_panel(cards)

        self._finish_telemetry(telemetry, leads, started)
        return leads

    def _finish_telemetry(self, telemetry: SearchTelemetry, leads: List[Lead], started: float):
        telemetry.search_duration_ms = (time.perf_counter() - started) * 1000
        telemetry.leads_returned = len(leads)
        telemetry.leads_with_website = sum(1 for lead in leads if lead.website)
        if MAPS_TELEMETRY_ENABLED:
            self.last_telemetry = telemetry.as_dict()

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeoutError),
    )
    def _attempt_navigation(self, url: str) -> bool:
        """Navigate to URL and wait for feed. Returns False if no results."""
        self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self._accept_cookies()
        return self._wait_for_feed()

    def _accept_cookies(self):
        selectors = [
            'button:has-text("Accept all")',
            'button:has-text("I agree")',
            'button:has-text("Accept")',
            'button:has-text("Agree")',
            'button[aria-label="Accept all"]',
        ]
        for selector in selectors:
            with contextlib.suppress(Exception):
                btn = self._page.locator(selector)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    self._page.wait_for_timeout(500)
                    break

    def _wait_for_feed(self) -> bool:
        """Wait for results feed to load. Returns False if no results found."""
        # First, wait for either the results feed OR a "no results" indicator
        try:
            self._page.wait_for_selector(
                ', '.join([
                    'div[role="feed"]',
                    'div[aria-label*="Results"]',
                    'section[aria-label*="Results"]',
                    # "No results" indicators
                    'div.fontBodyMedium:has-text("No results found")',
                    'div:has-text("didn\'t match any")',
                    'div[class*="section-no-result"]',
                ]),
                timeout=20000,  # Reduced from 40s since we detect no-results early
            )
        except Exception:
            return False
        
        # Check if we got a "no results" page
        no_results_markers = [
            "No results found",
            "didn't match any",
            "couldn't find",
            "0 results",
        ]
        try:
            page_text = self._page.inner_text('body')
            if any(marker.lower() in page_text.lower() for marker in no_results_markers):
                return False
        except Exception:
            pass
        
        return True

    def _cards_locator(self):
        return self._page.locator('div[role="feed"] div.Nv2PK')

    def _scroll_results_panel(self, cards_locator):
        with contextlib.suppress(Exception):
            cards_locator.first.scroll_into_view_if_needed()
        with contextlib.suppress(Exception):
            self._page.mouse.wheel(0, 1600)
        self._page.wait_for_timeout(SCROLL_WAIT_MS)

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def _extract_card(self, card, keyword: str, location: str, telemetry: Optional[SearchTelemetry] = None) -> Optional[Lead]:
        try:
            name = self._extract_name(card)
            if not name:
                return None

            place_url = card.locator('a[href*="/maps/place/"]').first.get_attribute("href")
            website = self._extract_card_website(card)

            if not website:
                previous_sidebar = self._sidebar_signature()
                card.click(timeout=CARD_CLICK_TIMEOUT_MS)
                if telemetry:
                    telemetry.sidebar_click_attempts += 1
                wait_started = time.perf_counter()
                self._wait_for_sidebar_ready(previous_sidebar)
                if telemetry:
                    telemetry.sidebar_wait_duration_ms += (time.perf_counter() - wait_started) * 1000
                website = self._extract_sidebar_website()
                if telemetry:
                    if website:
                        telemetry.sidebar_click_successes += 1
                    else:
                        telemetry.sidebar_extraction_failures += 1

            if website:
                if is_aggregator(website):
                    website = None

            return Lead(name=name, website=website, location=location, keyword=keyword, place_url=place_url)
        except PlaywrightTimeoutError:
            return None
        except Exception:
            return None

    def _read_visible_card_data(self, cards_locator) -> list[dict]:
        with contextlib.suppress(Exception):
            return cards_locator.evaluate_all(
                """
                (cards) => cards.map((card) => {
                  const place = card.querySelector('a[href*="/maps/place/"]');
                  const links = Array.from(card.querySelectorAll('a[href^="http"]'));
                  const website = links
                    .map((a) => a.href || "")
                    .find((href) => href.startsWith("http") && !href.includes("google.")) || "";
                  const label = (place && place.getAttribute("aria-label")) || "";
                  const firstLine = ((card.innerText || "").split("\\n")[0] || "").trim();
                  return {
                    name: (label || firstLine || "").trim(),
                    place_url: (place && place.href) || "",
                    website
                  };
                })
                """
            )
        return []

    def _lead_from_batch_card(self, data: dict, keyword: str, location: str) -> Optional[Lead]:
        if not isinstance(data, dict):
            return None
        name = str(data.get("name") or "").strip()
        place_url = str(data.get("place_url") or "").strip()
        website = str(data.get("website") or "").strip()
        if not (name and place_url and website):
            return None
        if is_aggregator(website):
            website = None
        if not website:
            return None
        return Lead(name=name, website=website, location=location, keyword=keyword, place_url=place_url)

    def _extract_name(self, card) -> Optional[str]:
        with contextlib.suppress(Exception):
            label = card.locator('a[href*="/maps/place/"]').first.get_attribute("aria-label")
            if label:
                return label.strip()
        with contextlib.suppress(Exception):
            text = card.inner_text()
            if text:
                return text.split("\n", 1)[0].strip()
        return None

    def _extract_card_website(self, card) -> Optional[str]:
        selectors = ["a[href^='http']:not([href*='google.'])"]
        for selector in selectors:
            with contextlib.suppress(Exception):
                links = card.locator(selector)
                for i in range(min(5, links.count())):
                    href = links.nth(i).get_attribute("href")
                    if href and href.startswith("http") and "google." not in href:
                        return href
        return None

    def _extract_sidebar_website(self) -> Optional[str]:
        sidebar_selectors = [
            'a:has-text("Website")',
            'a[aria-label*="Website"]',
            'a[data-item-id="authority"]',
            'a[href^="http"]:has-text("Website")',
        ]
        for selector in sidebar_selectors:
            with contextlib.suppress(Exception):
                link = self._page.locator(selector).first
                if link.count() and link.is_visible():
                    href = link.get_attribute("href")
                    if href and href.startswith("http") and "google." not in href:
                        return href
        with contextlib.suppress(Exception):
            main = self._page.locator('div[role="main"]').first
            links = main.locator('a[href^="http"]:not([href*="google"])')
            for i in range(min(5, links.count())):
                href = links.nth(i).get_attribute("href")
                if href and href.startswith("http") and "google." not in href:
                    return href
        return None

    def _sidebar_signature(self) -> str:
        with contextlib.suppress(Exception):
            main = self._page.locator('div[role="main"]').first
            if main.count():
                return (main.inner_text(timeout=300) or "")[:500]
        return ""

    def _wait_for_sidebar_ready(self, previous_signature: str = ""):
        if not MAPS_ADAPTIVE_SIDEBAR_WAIT_ENABLED:
            self._page.wait_for_timeout(SIDEBAR_AFTER_CLICK_WAIT_MS)
            return
        selectors = [
            'a:has-text("Website")',
            'a[aria-label*="Website"]',
            'a[data-item-id="authority"]',
        ]
        deadline = time.perf_counter() + (SIDEBAR_ADAPTIVE_MAX_WAIT_MS / 1000)
        while time.perf_counter() < deadline:
            for selector in selectors:
                with contextlib.suppress(Exception):
                    locator = self._page.locator(selector).first
                    if locator.count() and locator.is_visible(timeout=100):
                        return
            if previous_signature:
                current_signature = self._sidebar_signature()
                if current_signature and current_signature != previous_signature:
                    return
            self._page.wait_for_timeout(100)

    # ------------------------------------------------------------------
    # Safeguards
    # ------------------------------------------------------------------

    def _check_captcha(self):
        try:
            html = self._page.content()
        except Exception:
            return
        markers = [
            "unusual traffic",
            "detected unusual traffic",
            "To continue, please verify",
            "solve the CAPTCHA",
        ]
        if any(marker.lower() in html.lower() for marker in markers):
            time.sleep(CAPTCHA_BACKOFF_S)
            raise CaptchaDetected("Captcha encountered")
