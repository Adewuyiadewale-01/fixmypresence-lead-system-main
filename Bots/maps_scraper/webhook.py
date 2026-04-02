from __future__ import annotations
from datetime import datetime, timezone
import time
from typing import Iterable, List
from common.google_client import GoogleClient
from maps_scraper.utils import Lead
from maps_scraper.config import WEBHOOK_MAX_RETRIES, WEBHOOK_RETRY_BACKOFF_S, WEBHOOK_URL

def _lead_to_row(lead: Lead) -> List[str]:
    return [lead.name, lead.website or "", lead.location, lead.keyword, lead.place_url or "", lead.status]

def send_batch_to_webhook(batch_number: int, leads: Iterable[Lead]) -> bool:
    leads_list = list(leads)
    ts = datetime.now(timezone.utc).isoformat()
    batch_id = f"batch-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{batch_number:03d}"
    
    # User Sheet Order: name, website, location, keyword, place_url, timestamp, batch_id, Website Status
    rows = []
    for ld in leads_list:
        # Base lead data (excluding status)
        base = [ld.name, ld.website or "", ld.location, ld.keyword, ld.place_url or ""]
        # Match configured script expectation (8 columns): Name, Web, Loc, Key, URL, TS, BatchID, Status
        rows.append(base + [ts, batch_id, ld.status])

    payload = {"batch_id": batch_id, "rows": rows}
    
    for attempt in range(1, WEBHOOK_MAX_RETRIES + 1):
        if GoogleClient.post_sync(payload, webhook_url=WEBHOOK_URL):
            print(f"[Webhook] OK (batch_id={batch_id})")
            return True
        if attempt < WEBHOOK_MAX_RETRIES:
            delay = WEBHOOK_RETRY_BACKOFF_S * attempt
            print(f"[Webhook] RETRYING batch {batch_number} in {delay}s (attempt {attempt}/{WEBHOOK_MAX_RETRIES})")
            time.sleep(delay)

    print(f"[Webhook] FAILED: batch {batch_number}")
    return False
