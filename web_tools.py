"""Small stdio MCP servers for free metasearch and a local Firecrawl v2 API."""

import ipaddress
import os
import re
import socket
import sys
from urllib.parse import urlsplit
from uuid import UUID

import requests
from ddgs import DDGS
from mcp.server.fastmcp import FastMCP

server = FastMCP("Workbench web")
API = "http://127.0.0.1:3002/v2"
LOCAL = requests.Session()
LOCAL.trust_env = False
LIMIT = 45000
NAMES = ("localhost", "host.docker.internal", "gateway.docker.internal")
SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
# Firecrawl parses URLs the way browsers do: a backslash ends the host and a percent-encoded host is decoded.
# urlsplit does neither, so such URLs are refused and the host checked here is the host that gets fetched.
ODD = re.compile(r"[\s\\\x00-\x1f\x7f]")


def clip(text):
    if len(text) <= LIMIT:
        return text
    return text[:LIMIT] + f"\n\n[Truncated at {LIMIT:,} of {len(text):,} characters.]"


def private_host(host, resolve=socket.getaddrinfo):
    """True when a host names this computer or a private network, by name or by any address it resolves to."""
    host = host.strip("[]").rstrip(".").lower()
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii").rstrip(".").lower()
        except UnicodeError:
            return True
    if host in NAMES or host.endswith(SUFFIXES):
        return True

    def private(text):
        ip = ipaddress.ip_address(text.split("%")[0])
        ip = getattr(ip, "ipv4_mapped", None) or ip
        return not ip.is_global

    try:
        return private(host)
    except ValueError:
        pass
    if "." not in host:
        return True
    try:
        rows = resolve(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return False
    return any(private(r[4][0]) for r in rows)


def public_url(url):
    url = url.strip()
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme not in {"http", "https"} or not host:
        raise ValueError("Enter an HTTP or HTTPS URL.")
    if ODD.search(url) or "%" in host:
        raise ValueError(
            "Enter the URL without spaces or backslashes, and with its host name written out "
            "instead of percent-encoded."
        )
    try:
        host.encode("idna")
    except UnicodeError:
        raise ValueError("Enter an HTTP or HTTPS URL with a valid host name.") from None
    if os.environ.get("WORKBENCH_WEB_PRIVATE") != "1" and private_host(host):
        raise ValueError(
            "That address is on this computer or a private network. Use a public URL, or ask the user "
            "to set WORKBENCH_WEB_PRIVATE=1 before starting Agent Workbench."
        )
    return url


def firecall(method, path, payload=None):
    try:
        r = LOCAL.request(method, API + path, json=payload, timeout=100)
    except requests.ConnectionError:
        raise ValueError(
            "Firecrawl is not running on 127.0.0.1:3002. Ask the user to start page crawling in "
            "Agent Workbench, then retry."
        ) from None
    except requests.Timeout:
        raise ValueError(
            "Firecrawl did not answer within 100 seconds. Retry with a smaller page or crawl limit."
        ) from None
    try:
        data = r.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not r.ok or not data.get("success"):
        raise ValueError(
            str(data.get("error") or f"Firecrawl request failed with HTTP {r.status_code}.")
        )
    return data


def web_search(query: str, limit: int = 5) -> list[dict]:
    """Search public web engines without a paid API key. Return titles, URLs and snippets."""
    return DDGS(timeout=15).text(query, max_results=max(1, min(limit, 12)))


def scrape(url: str) -> str:
    """Read a public web page as Markdown using the local Firecrawl service."""
    data = firecall(
        "POST", "/scrape", {"url": public_url(url), "formats": ["markdown"]}
    )
    return clip(data["data"]["markdown"])


def crawl(url: str, limit: int = 5) -> dict:
    """Queue a small website crawl. Use crawl_status with the returned ID to read pages."""
    return firecall(
        "POST",
        "/crawl",
        {
            "url": public_url(url),
            "limit": max(1, min(limit, 25)),
            "scrapeOptions": {"formats": ["markdown"]},
        },
    )


def crawl_status(job_id: str, page: int = 0) -> dict:
    """Read crawl status and one collected page, indexed from zero."""
    data = firecall("GET", "/crawl/" + str(UUID(job_id)))
    docs = data.pop("data", [])
    data.pop("next", None)
    if 0 <= page < len(docs):
        doc = docs[page]
        data["page"] = {
            "url": doc.get("metadata", {}).get("sourceURL"),
            "markdown": clip(doc.get("markdown", "")),
        }
    return data


if __name__ == "__main__":
    tools = {"search": [web_search], "firecrawl": [scrape, crawl, crawl_status]}[
        sys.argv[1]
    ]
    for tool in tools:
        server.tool()(tool)
    server.run()
