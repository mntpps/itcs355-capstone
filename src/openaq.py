"""The only code that talks to OpenAQ. Retries, backoff and the rate limit live here."""
import os
import time

import requests
from dotenv import load_dotenv

BASE = "https://api.openaq.org/v3"
PAUSE = 1.9            # limit is 2,000 requests/hour = one per 1.8 s; stay just under
RETRY_ON = {429, 500, 502, 503, 504}

_session = None


def _session_with_key():
    global _session
    if _session is None:
        load_dotenv()
        _session = requests.Session()
        _session.headers["X-API-Key"] = os.environ["OPENAQ_API_KEY"]
    return _session


def get(path: str, params: dict, tries: int = 5) -> dict:
    for attempt in range(tries):
        time.sleep(PAUSE)
        try:
            r = _session_with_key().get(BASE + path, params=params, timeout=90)
        except requests.RequestException as e:
            r, reason = None, type(e).__name__
        else:
            if r.status_code not in RETRY_ON:
                r.raise_for_status()      # 401/404 etc. are bugs, not bad luck: fail loudly
                return r.json()
            reason = r.status_code
        wait = 5 * 2 ** attempt           # 5, 10, 20, 40, 80 s
        print(f"  retry {attempt + 1}/{tries} in {wait}s ({reason})")
        time.sleep(wait)
    raise RuntimeError(f"OpenAQ {path} failed after {tries} tries")