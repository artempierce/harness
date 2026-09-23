"""The assistant's only two tools that reach outside the project. Both
return text wrapped in <fetched-content> — a web page or a search snippet is
the least trustworthy text this system will ever see, and it must be
visibly marked as data, never as an instruction. Same reasoning as the
<output> delimiter around judged text in ninja/judge.py.
"""

import ipaddress
import os
import socket
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

# Mirrors tools.py's MAX_READ: what a page read may put in the transcript.
MAX_FETCH = 20_000
SEARCH_RESULTS = 5
TIMEOUT = 10.0

# The SSRF surface: a hostname that resolves to any of these is refused
# before a request is made, regardless of what the URL's text looked like.
# 169.254.0.0/16 covers the cloud metadata address (169.254.169.254), the
# single most common real-world SSRF target.
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
]


def _guard(url: str) -> None:
    """Refuse a URL before any request is made: wrong scheme, or a host
    that resolves to a private/loopback/link-local address."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported url scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError(f"no host in url: {url}")
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, None)}
    except socket.gaierror as e:
        raise ValueError(f"could not resolve host: {parsed.hostname}") from e
    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        if any(ip in net for net in _PRIVATE_NETWORKS):
            raise ValueError(f"refusing a private/internal address: {addr}")


def fetch_url(url: str, http: httpx.Client) -> str:
    """Fetch a page and return its readable text. Redirects are reported,
    not followed — auto-following is the standard way an SSRF guard on the
    original URL gets bypassed by a redirect to an internal address."""
    _guard(url)
    response = http.get(url, follow_redirects=False, timeout=TIMEOUT)
    if response.is_redirect:
        location = response.headers.get("location", "(no Location header)")
        return (
            f"the page redirected to {location} — not followed; "
            f"fetch that url directly if it looks right"
        )
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("text/html"):
        text = BeautifulSoup(response.text, "html.parser").get_text(separator=" ", strip=True)
    elif content_type.startswith("text/plain"):
        text = response.text
    else:
        return f"not a readable page (content-type: {content_type or 'unknown'})"
    if len(text) > MAX_FETCH:
        text = f"{text[:MAX_FETCH]}\n[truncated: {len(text) - MAX_FETCH} more characters]"
    return f'<fetched-content source="{url}">\n{text}\n</fetched-content>'


def search_web(query: str, http: httpx.Client) -> str:
    """Search the web via Tavily and return the top results. Read at call
    time (not a module constant) so tests can set/unset it per case."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise ValueError("web search isn't configured — set TAVILY_API_KEY")
    response = http.post(
        "https://api.tavily.com/search",
        json={"api_key": api_key, "query": query},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    results = response.json().get("results", [])[:SEARCH_RESULTS]
    lines = [
        f"{r.get('title', '(no title)')} — {r.get('url', '')} — {r.get('content', '')}"
        for r in results
    ]
    body = "\n".join(lines) if lines else "no results"
    return f'<fetched-content source="tavily">\n{body}\n</fetched-content>'
