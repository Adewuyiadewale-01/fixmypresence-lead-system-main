from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, List

import httpx


_TIMEOUT_S = 8.0
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
}


def _candidate_urls(url: str) -> List[str]:
    cleaned = url.strip()
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return [cleaned]
    return [f"https://{cleaned}", f"http://{cleaned}"]


def _is_live_status(status_code: int) -> bool:
    if 200 <= status_code < 400:
        return True
    if status_code in {401, 403, 405, 406, 429}:
        return True
    if 400 <= status_code < 500 and status_code not in {404, 410}:
        return True
    return False


def get_website_status(url: str) -> str:
    """
    Phase 1 validation.
    Returns: 'Live', 'Dead', or 'NO SITE'.
    """
    if not url:
        return "NO SITE"

    explicit_dead = False

    try:
        with httpx.Client(timeout=_TIMEOUT_S, verify=False, follow_redirects=True) as client:
            for target_url in _candidate_urls(url):
                try:
                    resp = client.get(target_url, headers=_HEADERS)
                except Exception:
                    continue

                if _is_live_status(resp.status_code):
                    return "Live"

                if resp.status_code in {404, 410} or resp.status_code >= 500:
                    explicit_dead = True
    except Exception:
        pass

    return "Dead" if explicit_dead else "Dead"


def get_website_statuses(urls: Iterable[str], max_workers: int = 8) -> List[str]:
    """Resolve multiple website statuses concurrently while preserving order."""
    url_list = list(urls)
    if not url_list:
        return []
    workers = max(1, min(max_workers, len(url_list)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(get_website_status, url_list))
