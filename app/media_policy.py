"""Limits shared by the public converter and its isolated download process."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

MEDIA_HOSTS = (
    "youtube.com", "youtu.be", "vimeo.com", "tiktok.com", "instagram.com",
    "facebook.com", "fb.watch", "soundcloud.com", "bandcamp.com",
    "dailymotion.com", "dai.ly", "twitch.tv", "archive.org", "ted.com",
    "reddit.com", "redd.it", "x.com", "twitter.com", "bsky.app",
    "wikimedia.org", "pinterest.com", "tumblr.com", "bilibili.com",
    "rumble.com", "mixcloud.com", "podcasts.apple.com",
)


def source_limitation(hostname: str, path: str) -> str | None:
    if hostname == "spotify.com" or hostname.endswith(".spotify.com") or hostname == "spotify.link":
        return "Spotify links cannot be converted to full MP3 files here. Use Spotify's offline feature or a downloadable file from the artist."
    if hostname == "music.apple.com" and "/post/" not in path:
        return "Apple Music subscription songs cannot be converted here. Public Apple Podcasts links and your own unprotected audio files are supported."
    return None


def validate_url(value: str, *, source: bool = True) -> str:
    value = value.strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
        hostname = (parsed.hostname or "").encode("idna").decode("ascii").lower()
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Enter a valid media link.") from exc
    if (len(value) > 2048 or not hostname or parsed.scheme not in ("https", "http")
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 80, 443) or "\\" in value
            or any(ord(c) < 33 or ord(c) == 127 for c in value)):
        raise ValueError("Use a public HTTP or HTTPS media link without login details.")
    # Any public web source may be handled by yt-dlp's site extractors or its
    # generic embedded/direct-media extractor. MEDIA_HOSTS is just UI examples.
    if source and (limitation := source_limitation(hostname, parsed.path)):
        raise ValueError(limitation)
    # Private IP literals and local names are never valid media endpoints.
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if "." not in hostname or hostname.endswith((".local", ".localhost", ".internal")):
            raise ValueError("Local network addresses are not supported.")
    else:
        if not public_ip(address):
            raise ValueError("Local network addresses are not supported.")
    return value


def public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return public_ip(address.ipv4_mapped)
        # Avoid transition networks that can embed private IPv4 destinations.
        if address.sixtofour is not None or address.teredo is not None:
            return False
        if address in ipaddress.ip_network("64:ff9b::/96"):
            return False
    return address.is_global and not address.is_multicast


def guarded_resolver(resolver):
    """Check the exact DNS answers used to connect, including redirect targets."""
    def resolve(host, port, *args, **kwargs):
        if port not in (80, 443, "80", "443", "http", "https"):
            raise OSError("Only web ports are allowed")
        answers = resolver(host, port, *args, **kwargs)
        if not answers or any(not public_ip(ipaddress.ip_address(a[4][0])) for a in answers):
            raise OSError("Blocked a non-public network destination")
        return answers
    return resolve


def install_network_guard() -> None:
    # The runner exclusively uses yt-dlp's stdlib urllib transport. Do not enable
    # curl_cffi, browser impersonation, proxies, or external media downloaders.
    socket.getaddrinfo = guarded_resolver(socket.getaddrinfo)
