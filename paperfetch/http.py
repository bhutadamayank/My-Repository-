"""HTTP session shared by every request (keeps publisher cookies between hops)."""

from __future__ import annotations

from http.cookiejar import MozillaCookieJar

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import __version__
from .config import Config

# We identify ourselves honestly. Publishers that refuse non-browser clients
# (bot checks, rate limits) are respected: the user gets the link to open in a
# real browser instead.
USER_AGENT = f"Mozilla/5.0 (compatible; paperfetch/{__version__})"


def make_session(cfg: Config) -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=2,
        backoff_factor=0.8,
        backoff_max=8,
        # 429 (rate limit) is deliberately NOT retried: we back off and move on.
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
        raise_on_status=False,
        # Some APIs send Retry-After of many hours (daily quotas); never sleep on it.
        respect_retry_after_header=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    if cfg.cookies_file and cfg.cookies_file.is_file():
        jar = MozillaCookieJar(str(cfg.cookies_file))
        jar.load(ignore_discard=True, ignore_expires=True)
        s.cookies.update(jar)
    return s


def api_headers(cfg: Config) -> dict[str, str]:
    """Identify ourselves honestly to scholarly metadata APIs."""
    contact = f" (mailto:{cfg.email})" if cfg.email else ""
    return {"User-Agent": f"paperfetch/{__version__}{contact}", "Accept": "application/json"}
