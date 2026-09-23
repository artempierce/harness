"""Web search & fetch — the least trustworthy text this system will ever
see. The SSRF and scheme guards here are security tests, in the same sense
tests/test_tools.py's path-boundary tests are: do not relax them to make a
feature work."""

import httpx
import pytest

from ninja import web


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _html(request):
    return httpx.Response(
        200,
        headers={"content-type": "text/html"},
        text="<html><body><p>hello <b>world</b></p></body></html>",
    )


def test_fetch_url_returns_readable_text_wrapped_in_a_delimiter():
    out = web.fetch_url("https://example.com/page", _client(_html))
    assert '<fetched-content source="https://example.com/page">' in out
    assert "hello world" in out
    assert out.strip().endswith("</fetched-content>")


def test_fetch_url_refuses_a_non_http_scheme():
    with pytest.raises(ValueError, match="scheme"):
        web.fetch_url("file:///etc/passwd", _client(_html))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://172.16.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/",  # cloud metadata endpoint
        "http://localhost/",
    ],
)
def test_fetch_url_refuses_private_loopback_and_link_local_addresses(url):
    with pytest.raises(ValueError, match="private|internal"):
        web.fetch_url(url, _client(_html))


def test_fetch_url_refuses_ipv4_mapped_ipv6_loopback_addresses(monkeypatch):
    """Regression test: IPv4-mapped IPv6 addresses like ::ffff:127.0.0.1 must
    be caught by the SSRF guard, even though they don't match the old
    hand-rolled _PRIVATE_NETWORKS list."""
    def mock_getaddrinfo(host, port):
        # Simulate a hostname that resolves to IPv4-mapped IPv6 loopback
        return [(2, 1, 6, "", ("::ffff:127.0.0.1", 0))]

    monkeypatch.setattr("socket.getaddrinfo", mock_getaddrinfo)
    with pytest.raises(ValueError, match="private|internal"):
        web.fetch_url("https://internal-via-ipv6.example.com/", _client(_html))


def test_fetch_url_reports_a_redirect_without_following_it():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/internal"})

    out = web.fetch_url("https://example.com/redirector", _client(handler))
    assert "127.0.0.1/internal" in out
    assert len(seen) == 1  # the redirect target was never requested


def test_fetch_url_refuses_a_non_text_content_type():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")

    out = web.fetch_url("https://example.com/doc.pdf", _client(handler))
    assert "not a readable page" in out


def test_fetch_url_truncates_long_pages():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="x" * 25_000)

    out = web.fetch_url("https://example.com/big", _client(handler))
    assert "[truncated: 5000 more characters]" in out


def test_search_web_raises_when_not_configured(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        web.search_web("x", _client(_html))


def test_search_web_wraps_results_in_the_delimiter(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    def handler(request):
        return httpx.Response(
            200, json={"results": [{"title": "t", "url": "https://x.com", "content": "c"}]}
        )

    out = web.search_web("ninja agent", _client(handler))
    assert '<fetched-content source="tavily">' in out
    assert "https://x.com" in out
    assert out.strip().endswith("</fetched-content>")


def test_search_web_returns_at_most_five_results(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    def handler(request):
        results = [
            {"title": f"r{i}", "url": f"https://x.com/{i}", "content": "c"} for i in range(8)
        ]
        return httpx.Response(200, json={"results": results})

    out = web.search_web("ninja agent", _client(handler))
    assert out.count("https://x.com/") == 5
