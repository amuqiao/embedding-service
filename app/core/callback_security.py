from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


_METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
}


def _resolved_ips(hostname: str, port: int) -> set[ipaddress._BaseAddress]:
    try:
        addrinfos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("callback.url host could not be resolved") from exc
    return {ipaddress.ip_address(addrinfo[4][0]) for addrinfo in addrinfos}


def _is_forbidden_ip(address: ipaddress._BaseAddress) -> bool:
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address in _METADATA_IPS
    )


def validate_callback_url_security(url: str, *, allow_insecure_local: bool) -> None:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if not hostname:
        raise ValueError("callback.url must include host")
    if parsed.fragment:
        raise ValueError("callback.url must not include fragment")
    if parsed.username or parsed.password:
        raise ValueError("callback.url must not include user info")

    is_allowed_local = (
        allow_insecure_local
        and parsed.scheme == "http"
        and hostname in {"127.0.0.1", "localhost"}
    )
    if parsed.scheme != "https" and not is_allowed_local:
        raise ValueError("callback.url must be HTTPS")
    if parsed.port is not None and parsed.port != 443 and not is_allowed_local:
        raise ValueError("callback.url must use standard HTTPS port")

    port = parsed.port or (80 if parsed.scheme == "http" else 443)
    addresses = _resolved_ips(hostname, port)
    if not addresses:
        raise ValueError("callback.url host could not be resolved")
    if not is_allowed_local and any(_is_forbidden_ip(address) for address in addresses):
        raise ValueError("callback.url must not resolve to private or reserved network addresses")
