
import logging
import ssl
import aiohttp
import httpx
from typing import List, Dict, Any

from common.config import WEBHOOK_URL, WEBHOOK_SECRET, SOURCE_WEBHOOK_URL, OUTPUT_WEBHOOK_URL

logger = logging.getLogger(__name__)


def _webhook_response_is_success(text: str) -> bool:
    lower = (text or "").strip().lower()
    return any(token in lower for token in ["ok", "success", "added", "duplicate", "updated", "processed"])

class GoogleClient:
    """
    Unified client for interacting with the Google Apps Script Webhook.
    Supports both synchronous (httpx) and asynchronous (aiohttp) usage.
    """

    @staticmethod
    def post_sync(data: Dict[str, Any], webhook_url: str = WEBHOOK_URL) -> bool:
        """Synchronous post to webhook (for headers/simple usage)."""
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

                if _webhook_response_is_success(text):
                    return True
                logger.warning(f"Google Script Response: {text}")
                return False
        except Exception as e:
            logger.error(f"Google Sync Post Failed: {e}")
            return False

    @staticmethod
    async def post_async(session: aiohttp.ClientSession, data: Dict[str, Any], webhook_url: str = WEBHOOK_URL) -> bool:
        """Asynchronous post to webhook (for high concurrency)."""
        payload = {
             "token": WEBHOOK_SECRET, # Redundant for some endpoints but safe
             **data
        }
        
        try:
            # Disable SSL verification (macOS Python SSL certificate issue often seen in these envs)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            async with session.post(webhook_url, json=payload, ssl=ssl_context) as response:
                if response.status == 200:
                    text = await response.text()
                    if _webhook_response_is_success(text):
                        logger.info(f"✅ Google Doc Updated: {text}")
                        return True
                    else:
                        logger.warning(f"⚠️ Google Script Warning: {text}")
                        return False
                else:
                    logger.error(f"❌ Google Upload Failed: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"❌ Google Async Post Failed: {e}")
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
                        return True
                    logger.warning(f"Source mark response warning: {text}")
                    return False
                logger.error(f"Failed to mark source processed: {response.status}")
                return False
        except Exception as e:
            logger.error(f"Error marking source processed: {e}")
            return False

    @staticmethod
    async def post_to_output(session: aiohttp.ClientSession, companies: List[dict]) -> bool:
        """Post grouped company+person results to the Employee_db output sheet."""
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
                    if _webhook_response_is_success(text):
                        logger.info(f"Output sheet updated: {text}")
                        return True
                    logger.warning(f"Output sheet response: {text}")
                    return False
                else:
                    logger.error(f"Output sheet upload failed: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Output sheet post failed: {e}")
            return False
