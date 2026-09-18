"""
Tests for app.core.safe_fetch - the SSRF-safe remote media fetcher.

These use mocked DNS resolution (socket.getaddrinfo) and a mocked httpx
transport, not real network calls - the point is to prove the *logic*
(what gets validated, when, and what actually gets connected to) is
correct, independent of network availability.
"""
import socket
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app.core.safe_fetch import (
    UnsafeUrlError,
    resolve_and_validate,
    safe_download,
)

# Captured before any test patches httpx.Client, so the helper below can
# still construct a real (test-transport-backed) client without recursing
# into whatever mock is active at call time.
_RealHttpxClient = httpx.Client


def _client_with_mock_transport(handler):
    return lambda **kw: _RealHttpxClient(
        transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "transport"}
    )


def _fake_addrinfo(ip: str):
    """Builds a return value shaped like socket.getaddrinfo's real output."""
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (ip, 443))]


class TestResolveAndValidate:
    def test_rejects_unsupported_scheme(self):
        with pytest.raises(UnsafeUrlError, match="Unsupported URL scheme"):
            resolve_and_validate("ftp://example.com/file.jpg")

    def test_rejects_url_with_no_hostname(self):
        with pytest.raises(UnsafeUrlError, match="must contain a hostname"):
            resolve_and_validate("http:///path")

    def test_accepts_a_hostname_that_resolves_to_a_public_address(self):
        with patch("socket.getaddrinfo", return_value=_fake_addrinfo("93.184.216.34")):
            ip = resolve_and_validate("https://example.com/image.jpg")
        assert ip == "93.184.216.34"

    def test_rejects_a_hostname_that_resolves_to_loopback(self):
        with patch("socket.getaddrinfo", return_value=_fake_addrinfo("127.0.0.1")):
            with pytest.raises(UnsafeUrlError, match="non-public address"):
                resolve_and_validate("https://evil.example.com/x.jpg")

    def test_rejects_a_hostname_that_resolves_to_a_private_range(self):
        with patch("socket.getaddrinfo", return_value=_fake_addrinfo("10.0.0.5")):
            with pytest.raises(UnsafeUrlError, match="non-public address"):
                resolve_and_validate("https://evil.example.com/x.jpg")

    def test_rejects_link_local_cloud_metadata_address(self):
        # 169.254.169.254 is the AWS/GCP/Azure instance-metadata endpoint -
        # one of the most common real SSRF targets in practice.
        with patch("socket.getaddrinfo", return_value=_fake_addrinfo("169.254.169.254")):
            with pytest.raises(UnsafeUrlError, match="non-public address"):
                resolve_and_validate("https://evil.example.com/x.jpg")

    def test_rejects_when_any_answer_in_a_multi_address_response_is_private(self):
        # A malicious DNS response could mix one public and one private
        # address, hoping validation only checks the first. All addresses
        # in the answer set must be public.
        family = socket.AF_INET
        mixed = [
            (family, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (family, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
        ]
        with patch("socket.getaddrinfo", return_value=mixed):
            with pytest.raises(UnsafeUrlError, match="non-public address"):
                resolve_and_validate("https://evil.example.com/x.jpg")

    def test_dns_failure_raises_unsafe_url_error_not_a_raw_socket_error(self):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("no such host")):
            with pytest.raises(UnsafeUrlError, match="Could not resolve"):
                resolve_and_validate("https://does-not-exist.invalid/x.jpg")


class TestSafeDownload:
    def test_connects_to_the_validated_ip_not_a_freshly_resolved_hostname(self, tmp_path):
        """
        This is the actual DNS-rebinding regression test: resolve_and_validate
        is called once, and the URL that httpx actually connects to must be
        built from that already-validated IP - proving there's no second,
        unchecked resolution step between validation and connection.
        """
        dest = tmp_path / "out.jpg"
        captured_requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(
                200,
                headers={"content-type": "image/jpeg"},
                content=b"\xff\xd8\xff fake jpeg bytes",
            )

        with patch("socket.getaddrinfo", return_value=_fake_addrinfo("93.184.216.34")):
            with patch("httpx.Client", _client_with_mock_transport(handler)):
                safe_download("https://example.com/photo.jpg", dest)

        assert len(captured_requests) == 1
        # The connection URL's host is the pinned IP, not the original hostname.
        assert captured_requests[0].url.host == "93.184.216.34"
        # But the original hostname is preserved as the Host header, so
        # virtual-hosted/CDN-fronted targets still resolve correctly server-side.
        assert captured_requests[0].headers["host"] == "example.com"
        assert dest.read_bytes() == b"\xff\xd8\xff fake jpeg bytes"

    def test_rejects_a_redirect_to_an_internal_address(self, tmp_path):
        """
        Each hop of a redirect chain must be independently validated - a
        malicious server could return a 302 pointing at an internal address
        after the *original* URL passed validation.
        """
        dest = tmp_path / "out.jpg"
        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return httpx.Response(302, headers={"location": "http://internal.evil.example/x.jpg"})
            return httpx.Response(200, content=b"should never get here")

        def fake_getaddrinfo(host, *args, **kwargs):
            if host == "example.com":
                return _fake_addrinfo("93.184.216.34")
            return _fake_addrinfo("10.0.0.99")  # the redirect target resolves internally

        with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
            with patch("httpx.Client", _client_with_mock_transport(handler)):
                with pytest.raises(UnsafeUrlError, match="non-public address"):
                    safe_download("https://example.com/photo.jpg", dest)

        assert not dest.exists()

    def test_rejects_oversized_remote_media(self, tmp_path):
        dest = tmp_path / "out.jpg"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "image/jpeg"},
                content=b"x" * (200 * 1024 * 1024),  # 200MB, over the 100MB default cap
            )

        with patch("socket.getaddrinfo", return_value=_fake_addrinfo("93.184.216.34")):
            with patch("httpx.Client", _client_with_mock_transport(handler)):
                with pytest.raises(UnsafeUrlError, match="exceeds"):
                    safe_download("https://example.com/huge.jpg", dest)
