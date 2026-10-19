"""GCP Cloud Function (gen2, HTTP): fetch a URL with browser impersonation, return HTML.

Acts as a fetch proxy for the pricing module. NOTE: egress is from Google datacenter IPs,
which Amazon frequently blocks — this is deployed to measure that in practice. Returns the
upstream status in the X-Upstream-Status header so the caller can see 200 vs blocked.
"""

import os
import time

import functions_framework
from curl_cffi import requests as cffi

IMPERSONATE = os.environ.get("IMPERSONATE", "chrome110")
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "4"))
SECRET = os.environ.get("FETCH_SECRET", "")
# Datacenter egress is blocked by Amazon; route through a residential proxy when set.
PROXY_URL = os.environ.get("PROXY_URL", "")

_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-IN,en;q=0.9",
    "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"),
}


@functions_framework.http
def fetch(request):
    if SECRET and request.headers.get("X-Fetch-Secret") != SECRET:
        return ("forbidden", 403)
    url = request.args.get("url")
    if not url:
        return ("missing ?url", 400)

    proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
    last = "0"
    for _ in range(MAX_RETRIES):
        try:
            r = cffi.get(url, impersonate=IMPERSONATE, headers=_HEADERS, timeout=30,
                         verify=False, proxies=proxies)
            last = str(r.status_code)
            if r.status_code == 200 and "data-asin" in r.text:
                return (r.text, 200, {"Content-Type": "text/html; charset=utf-8",
                                      "X-Upstream-Status": "200", "X-Proxied": str(bool(proxies))})
        except Exception as e:  # surface proxy/transport errors for debugging
            last = f"err:{type(e).__name__}"
        time.sleep(0.5)
    return ("upstream blocked", 502, {"X-Upstream-Status": last, "X-Proxied": str(bool(proxies))})
