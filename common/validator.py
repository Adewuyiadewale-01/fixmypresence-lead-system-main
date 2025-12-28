
import httpx
import logging

def get_website_status(url: str) -> str:
    """
    Phase 1 Validation:
    1. Checks if website exists.
    2. Checks if website is live.
    Returns: 'Live', 'Dead', or 'NO SITE'.
    """
    # 1. CONFIRM PRESENCE
    if not url:
        return "NO SITE"
    # ...
    # if 200 <= resp.status_code < 400:
    #     return "Live"
    # else:
    #     return "Dead"
    
    # 2. CHECK LIVENESS
    if not url.startswith("http"):
        target_url = f"http://{url}"
    else:
        target_url = url

    try:
        # Timeout is fast (5s) because we are doing this live
        # verify=False to avoid SSL errors on some sites
        with httpx.Client(timeout=5.0, verify=False) as client:
            # mimic browser to avoid instant block
            resp = client.get(target_url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
            if 200 <= resp.status_code < 400:
                return "Live"
            else:
                return "Dead" # Dead link (404, 500, etc)
    except Exception:
        return "Dead" # Connection failed (DNS error, timeout)
