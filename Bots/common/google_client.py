
import json as _json
import logging
import ssl
import aiohttp
import httpx
from typing import List, Dict, Any, Optional

from common.config import WEBHOOK_URL, WEBHOOK_SECRET, SOURCE_WEBHOOK_URL, OUTPUT_WEBHOOK_URL
from common.system_config import get_managed_sheet_capacity_status, get_system_config

logger = logging.getLogger(__name__)


def _output_sheet_is_blocked() -> Optional[Dict[str, Any]]:
    try:
        config = get_system_config(include_live=False)
        entries = (((config.get("managed_sheets") or {}).get("entries")) if isinstance(config, dict) else None) or []
        output_entry = next((entry for entry in entries if isinstance(entry, dict) and entry.get("key") == "output"), None)
        if not output_entry:
            return None
        status = get_managed_sheet_capacity_status(output_entry, config=config)
        return status if status.get("blocked") else None
    except Exception:
        return None


def _webhook_response_is_success(text: str) -> bool:
    lower = (text or "").strip().lower()
    # Try JSON first (output script now returns JSON)
    try:
        data = _json.loads(text)
        status = (data.get("status") or "").lower()
        if status in ("ok", "success"):
            return True
        if status in ("error", "unauthorized"):
            return False
    except Exception:
        pass
    return any(token in lower for token in ["ok", "success", "added", "duplicate", "updated", "processed"])


def _parse_output_response(text: str) -> Optional[Dict]:
    """Parse the JSON response from the idempotent output script."""
    try:
        return _json.loads(text)
    except Exception:
        return None


def _parse_webhook_response(text: str) -> Optional[Dict[str, Any]]:
    try:
        data = _json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _normalize_output_results(companies: List[dict], parsed: Optional[Dict], raw_text: str) -> Dict[str, Any]:
    """Return a structured output-write result with per-company statuses."""
    company_ids = [(c.get("id") or "").strip() for c in companies]
    if parsed and parsed.get("status") == "ok":
        results = parsed.get("results") or []
        result_map = {}
        for item in results:
            company_id = (item.get("id") or "").strip()
            if company_id:
                result_map[company_id] = item
        normalized = []
        for company_id in company_ids:
            item = result_map.get(company_id) or {"id": company_id, "status": "unknown"}
            normalized.append(item)
        ok_statuses = {"inserted", "updated", "duplicate_skipped", "legacy_success"}
        return {
            "ok": all((item.get("status") or "") in ok_statuses for item in normalized),
            "results": normalized,
            "inserted": parsed.get("inserted", 0),
            "updated": parsed.get("updated", 0),
            "raw_text": raw_text,
            "retryable": False,
        }

    if _webhook_response_is_success(raw_text):
        return {
            "ok": True,
            "results": [{"id": company_id, "status": "legacy_success"} for company_id in company_ids],
            "inserted": 0,
            "updated": 0,
            "raw_text": raw_text,
            "retryable": False,
        }

    return {
        "ok": False,
        "results": [{"id": company_id, "status": "failed"} for company_id in company_ids],
        "inserted": 0,
        "updated": 0,
        "raw_text": raw_text,
        "retryable": False,
    }


class GoogleClient:
    """
    Unified client for interacting with the Google Apps Script Webhook.
    Supports both synchronous (httpx) and asynchronous (aiohttp) usage.
    """

    @staticmethod
    def post_sync(data: Dict[str, Any], webhook_url: str = WEBHOOK_URL) -> bool:
        """Synchronous post to webhook (for headers/simple usage)."""
        result = GoogleClient.post_sync_detailed(data, webhook_url=webhook_url)
        return bool(result.get("ok"))

    @staticmethod
    def post_sync_detailed(data: Dict[str, Any], webhook_url: str = WEBHOOK_URL) -> Dict[str, Any]:
        """Synchronous post to webhook with parsed response details."""
        payload = {
            "token": WEBHOOK_SECRET,
            **data
        }
        try:
            timeout = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=10.0)
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.post(webhook_url, headers={"Content-Type": "application/json"}, json=payload)
                resp.raise_for_status()
                text = resp.text.strip()
                parsed = _parse_webhook_response(text)

                if _webhook_response_is_success(text):
                    return {
                        "ok": True,
                        "status_code": resp.status_code,
                        "text": text,
                        "parsed": parsed,
                    }
                logger.warning(f"Google Script Response: {text}")
                return {
                    "ok": False,
                    "status_code": resp.status_code,
                    "text": text,
                    "parsed": parsed,
                }
        except Exception as e:
            logger.error(f"Google Sync Post Failed: {e}")
            return {
                "ok": False,
                "status_code": None,
                "text": str(e),
                "parsed": None,
            }

    @staticmethod
    async def post_async(session: aiohttp.ClientSession, data: Dict[str, Any], webhook_url: str = WEBHOOK_URL) -> bool:
        """Asynchronous post to webhook (for high concurrency)."""
        payload = {
             "token": WEBHOOK_SECRET,
             **data
        }

        try:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            async with session.post(webhook_url, json=payload, ssl=ssl_context) as response:
                if response.status == 200:
                    text = await response.text()
                    if _webhook_response_is_success(text):
                        logger.info(f"Google Doc Updated: {text[:200]}")
                        return True
                    else:
                        logger.warning(f"Google Script Warning: {text[:200]}")
                        return False
                else:
                    logger.error(f"Google Upload Failed: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Google Async Post Failed: {e}")
            return False

    @staticmethod
    async def fetch_urls(session: aiohttp.ClientSession) -> List[dict]:
        """Fetch unprocessed URLs from Google Sheets via Apps Script (legacy endpoint)."""
        try:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            async with session.get(WEBHOOK_URL, ssl=ssl_context) as response:
                if response.status == 200:
                    data = await response.json()
                    urls = data.get("urls", [])
                    logger.info(f"Fetched {len(urls)} unprocessed URLs from Google Sheets")
                    return urls
                else:
                    logger.error(f"Failed to fetch URLs: {response.status}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching URLs from sheet: {e}")
            return []

    @staticmethod
    async def fetch_source_urls(session: aiohttp.ClientSession) -> List[dict]:
        """Fetch unprocessed URLs from alpha/beta/gamma tabs via source webhook."""
        try:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            params = {"token": WEBHOOK_SECRET}
            async with session.get(SOURCE_WEBHOOK_URL, params=params, ssl=ssl_context) as response:
                if response.status == 200:
                    data = await response.json()
                    urls = data.get("urls", [])
                    logger.info(f"Fetched {len(urls)} unprocessed URLs from source sheet")
                    return urls
                else:
                    logger.error(f"Failed to fetch source URLs: {response.status}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching source URLs: {e}")
            return []

    @staticmethod
    async def mark_source_processed(session: aiohttp.ClientSession, updates: List[dict]) -> bool:
        """Mark rows as processed in the source sheet."""
        result = await GoogleClient.mark_source_processed_detailed(session, updates)
        return bool(result.get("ok"))

    @staticmethod
    async def mark_source_processed_detailed(session: aiohttp.ClientSession, updates: List[dict]) -> Dict[str, Any]:
        """Mark rows as processed in the source sheet and return retry metadata."""
        payload = {
            "token": WEBHOOK_SECRET,
            "action": "mark_processed",
            "updates": updates,
        }
        try:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            async with session.post(SOURCE_WEBHOOK_URL, json=payload, ssl=ssl_context) as response:
                if response.status == 200:
                    text = await response.text()
                    if _webhook_response_is_success(text):
                        return {"ok": True, "retryable": False, "raw_text": text}
                    logger.warning(f"Source mark response warning: {text}")
                    return {"ok": False, "retryable": False, "raw_text": text}
                logger.error(f"Failed to mark source processed: {response.status}")
                return {
                    "ok": False,
                    "retryable": response.status == 429 or response.status >= 500,
                    "raw_text": str(response.status),
                }
        except Exception as e:
            logger.error(f"Error marking source processed: {e}")
            return {"ok": False, "retryable": True, "raw_text": str(e)}

    @staticmethod
    async def post_to_output(session: aiohttp.ClientSession, companies: List[dict]) -> bool:
        """Post grouped company+person results to the Employee_db output sheet.

        The output Apps Script is idempotent: it deduplicates on company ID,
        returning per-company status (inserted/updated/duplicate_skipped).
        """
        payload = {
            "token": WEBHOOK_SECRET,
            "companies": companies,
        }
        try:
            result = await GoogleClient.post_to_output_detailed(session, companies)
            return bool(result.get("ok"))
        except Exception as e:
            logger.error(f"Output sheet post failed: {e}")
            return False

    @staticmethod
    async def post_to_output_detailed(session: aiohttp.ClientSession, companies: List[dict]) -> Dict[str, Any]:
        """Post grouped company+person results and return per-company statuses."""
        blocked_status = _output_sheet_is_blocked()
        if blocked_status:
            raw_text = (
                f"Output sheet {blocked_status.get('sheet_label')} / {blocked_status.get('tab_name')} "
                f"is at stop threshold ({blocked_status.get('current_rows')}/{blocked_status.get('stop_row_threshold')})."
            )
            return {
                "ok": False,
                "results": [{"id": (c.get('id') or '').strip(), "status": "failed"} for c in companies],
                "inserted": 0,
                "updated": 0,
                "raw_text": raw_text,
                "retryable": False,
            }

        payload = {
            "token": WEBHOOK_SECRET,
            "companies": companies,
        }
        try:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            async with session.post(OUTPUT_WEBHOOK_URL, json=payload, ssl=ssl_context) as response:
                if response.status == 200:
                    text = await response.text()
                    parsed = _parse_output_response(text)
                    normalized = _normalize_output_results(companies, parsed, text)
                    if normalized.get("ok"):
                        logger.info(
                            f"Output sheet updated: {normalized.get('inserted', 0)} inserted, "
                            f"{normalized.get('updated', 0)} updated"
                        )
                    else:
                        logger.warning(f"Output sheet response: {text[:200]}")
                    return normalized
                logger.error(f"Output sheet upload failed: {response.status}")
                return {
                    "ok": False,
                    "results": [{"id": (c.get('id') or '').strip(), "status": "failed"} for c in companies],
                    "inserted": 0,
                    "updated": 0,
                    "raw_text": str(response.status),
                    "retryable": response.status == 429 or response.status >= 500,
                }
        except Exception as e:
            logger.error(f"Output sheet post failed: {e}")
            return {
                "ok": False,
                "results": [{"id": (c.get('id') or '').strip(), "status": "failed"} for c in companies],
                "inserted": 0,
                "updated": 0,
                "raw_text": str(e),
                "retryable": True,
            }
