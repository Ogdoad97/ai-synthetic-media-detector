"""
SSRF-safe remote media fetching.

The previous implementation (still worth understanding, since this module
replaces it) validated the target hostname's resolved IP addresses against
a private/loopback/reserved blocklist, then made a normal httpx request
against the hostname. That has a real gap: httpx re-resolves the hostname
itself when it opens the connection, which happens *after* validation. A
DNS record with a very short TTL (or a resolver that returns different
answers on successive queries - both trivial for an attacker who controls
the domain) can pass the check with a public IP and then have the actual
connection resolve to an internal address instead. This is the standard
"DNS rebinding" SSRF bypass, not a hypothetical.

The fix: resolve the hostname exactly once, validate every address in that
single answer set, and then connect directly to one of the validated IPs -
never re-resolving. The original hostname is still sent as the Host header
(and as the TLS SNI value for HTTPS) so virtual-hosted/CDN-fronted targets
keep working and certificate validation still checks against the real
hostname, not the IP.
"""
from __future__ import annotations

import ipaddress
import socket
from pathlib import Path
from typing import Iterable, Set
from urllib.parse import urlparse, urljoin

import httpx

from app.core.config import get_settings


class UnsafeUrlError(ValueError):
    """Raised when a target URL fails SSRF safety validation."""


def _resolve_addresses(host: str) -> Set[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError("Could not resolve remote hostname") from exc
    return {info[4][0] for info in infos}


def _validate_addresses(addresses: Iterable[str]) -> None:
    if not addresses:
        raise UnsafeUrlError("Hostname resolved to no addresses")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise UnsafeUrlError(f"Remote URL resolves to a non-public address ({address})")


def resolve_and_validate(url: str) -> str:
    """
    Resolve `url`'s hostname once, validate every returned address is
    public, and return one validated IP to connect to. Raises
    UnsafeUrlError on any scheme/host/address problem.
    """
    settings = get_settings()
    parsed = urlparse(url)
    if parsed.scheme not in settings.URL_ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"Unsupported URL scheme: {parsed.scheme}")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("URL must contain a hostname")

    addresses = _resolve_addresses(host)
    _validate_addresses(addresses)
    # Any address in a validated set is safe to use - they were all checked
    # above. Picking the first is arbitrary but deterministic per call.
    return next(iter(addresses))


def safe_download(url: str, dest: Path) -> None:
    """
    Download public media with size and redirect guards, resistant to both
    plain SSRF (fetching an internal address directly) and DNS-rebinding
    SSRF (an internal address swapped in between validation and connection).

    Each hop of a redirect chain is independently resolved, validated, and
    then connected-to by pinned IP - a redirect can't point at an internal
    host any more than the original URL could.
    """
    settings = get_settings()
    current = url
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    with httpx.Client(timeout=settings.URL_FETCH_TIMEOUT_SEC, follow_redirects=False) as client:
        for _ in range(settings.URL_MAX_REDIRECTS + 1):
            parsed = urlparse(current)
            validated_ip = resolve_and_validate(current)

            # Connect to the pinned, already-validated IP - not the
            # hostname - by substituting it into the request URL, while
            # keeping the original hostname as the Host header (required
            # for virtual-hosted targets) and as the TLS server name (so
            # certificate validation still checks the real domain, not the
            # IP literal).
            netloc = f"[{validated_ip}]" if ":" in validated_ip else validated_ip
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            pinned_url = parsed._replace(netloc=netloc).geturl()

            extensions = {"sni_hostname": parsed.hostname} if parsed.scheme == "https" else {}
            resp = client.get(
                pinned_url,
                headers={"Host": parsed.hostname},
                extensions=extensions,
            )

            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise UnsafeUrlError("Redirect response missing Location header")
                current = urljoin(current, location)
                continue

            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if not (content_type.startswith("image/") or content_type.startswith("video/")):
                pass  # logged by the caller if it wants to; not a hard failure on its own
            if len(resp.content) > max_bytes:
                raise UnsafeUrlError(f"Remote media exceeds {settings.MAX_UPLOAD_SIZE_MB} MB limit")
            dest.write_bytes(resp.content)
            return

    raise UnsafeUrlError("Too many redirects while fetching remote media")
