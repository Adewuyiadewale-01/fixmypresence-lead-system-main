
import logging
import ssl
import aiohttp
import httpx
from typing import List, Optional, Dict, Any, Union

from common.config import WEBHOOK_URL, WEBHOOK_SECRET

logger = logging.getLogger(__name__)

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
                
                if text.lower() not in ("ok", "duplicate", "success", "added"):
                    # Apps script might return "Success", "Added", or "ok: ..." depending on endpoint logic
                    # We accept these as positive.
                     if any(x in text.lower() for x in ["success", "added", "ok"]):
                         return True
                     logger.warning(f"Google Script Response: {text}")
                     return False
                return True
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
                    if any(x in text.lower() for x in ["success", "added", "ok", "duplicate"]):
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
        """Fetch unprocessed URLs from Google Sheets via Apps Script."""
        try:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            async with session.get(WEBHOOK_URL, ssl=ssl_context) as response:
                if response.status == 200:
                    data = await response.json()
                    urls = data.get("urls", [])
                    logger.info(f"📋 Fetched {len(urls)} unprocessed URLs from Google Sheets")
                    return urls
                else:
                    logger.error(f"❌ Failed to fetch URLs: {response.status}")
                    return []
        except Exception as e:
            logger.error(f"❌ Error fetching URLs from sheet: {e}")
            return []
