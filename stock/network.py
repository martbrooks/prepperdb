"""Bounded HTTPS to explicit public provider hosts, pinned to validated DNS answers."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import http.client
import ipaddress
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit

DNS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="provider-dns")
USER_AGENT = "prepperdb/0.1 (self-hosted household stock lookup)"


class ProviderError(Exception):
    def __init__(self, status=0, retry_after=60):
        self.status = status
        self.retry_after = max(60, min(retry_after, 3600))


def fetch(url, *, hosts, max_bytes=512000, timeout=5, image=False):
    deadline = time.monotonic() + timeout
    for redirect in range(3):
        parsed = urlsplit(url)
        if (
            len(url) > 2048
            or parsed.scheme != "https"
            or parsed.hostname not in hosts
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or parsed.fragment
            or (image and not parsed.path.startswith("/images/products/"))
        ):
            raise ProviderError()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError()
        future = DNS.submit(socket.getaddrinfo, parsed.hostname, 443, type=socket.SOCK_STREAM)
        try:
            answers = future.result(timeout=remaining)
        except (FutureTimeout, OSError):
            future.cancel()
            raise ProviderError() from None
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses or any(
            not ipaddress.ip_address(address).is_global for address in addresses
        ):
            raise ProviderError()
        host = parsed.hostname
        active_sockets = []

        class PinnedConnection(http.client.HTTPSConnection):
            def connect(self):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderError()
                raw = socket.create_connection((addresses[0], 443), timeout=remaining)
                try:
                    raw.settimeout(max(0.05, deadline - time.monotonic()))
                    self.sock = self._context.wrap_socket(raw, server_hostname=host)
                    active_sockets.append(self.sock)
                    self.sock.settimeout(max(0.05, deadline - time.monotonic()))
                except Exception:
                    raw.close()
                    raise

        conn = PinnedConnection(
            host,
            context=ssl.create_default_context(),
            timeout=max(0.1, deadline - time.monotonic()),
        )

        def abort():
            for active_socket in active_sockets:
                try:
                    active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                active_socket.close()

        timer = threading.Timer(max(0.05, deadline - time.monotonic()), abort)
        timer.daemon = True
        timer.start()
        try:
            conn.request(
                "GET",
                parsed.path + ("?" + parsed.query if parsed.query else ""),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            if conn.sock:
                conn.sock.settimeout(max(0.05, deadline - time.monotonic()))
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                if redirect == 2:
                    raise ProviderError()
                url = urljoin(url, response.getheader("Location", ""))
                continue
            if response.status != 200:
                retry = response.getheader("Retry-After", "60")
                raise ProviderError(response.status, int(retry) if retry.isdigit() else 60)
            length = response.getheader("Content-Length", "")
            if length.isdigit() and int(length) > max_bytes:
                raise ProviderError()
            chunks = []
            size = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderError()
                if conn.sock:
                    conn.sock.settimeout(remaining)
                chunk = response.read1(min(16384, max_bytes + 1 - size))
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    raise ProviderError()
        except (OSError, ssl.SSLError, http.client.HTTPException, ValueError):
            raise ProviderError() from None
        finally:
            timer.cancel()
            conn.close()
    raise ProviderError()
