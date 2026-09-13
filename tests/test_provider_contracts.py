import pytest

from app.media_policy import source_limitation, validate_url
from app.media_runner import error_code


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=BaW_jenozKc",
    "https://vimeo.com/76979871",
    "https://www.tiktok.com/@example/video/1234567890123456789",
    "https://www.instagram.com/reel/example/",
    "https://soundcloud.com/example/example",
    "https://podcasts.apple.com/us/podcast/example/id123?i=456",
    "https://upload.wikimedia.org/example.mp4",
])
def test_named_public_source_families_pass_url_policy(url):
    assert validate_url(url) == url


def test_subscription_music_routes_to_lookup_instead_of_direct_download():
    assert source_limitation("open.spotify.com", "/track/example")
    assert source_limitation("music.apple.com", "/us/album/example/123")
    assert source_limitation("podcasts.apple.com", "/us/podcast/example/id123") is None


@pytest.mark.parametrize("message,code", [
    ("Sign in to confirm you’re not a bot", "host_blocked"),
    ("This private video requires login", "sign_in_required"),
    ("HTTP Error 403: Forbidden", "source_forbidden"),
    ("Requested format is not available", "format_unavailable"),
    ("Unsupported URL", "unsupported_source"),
    ("Video unavailable", "source_unavailable"),
])
def test_provider_failures_keep_stable_codes(message, code):
    assert error_code(Exception(message)) == code
