"""Resolve public song metadata to candidates, never silently replace a recording."""
import json
import re
import sys
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from app.media_policy import install_network_guard, validate_url
from app.media_runner import PublicYoutubeDL, QuietLogger, friendly_error


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.capture = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        self.capture = tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__"

    def handle_endtag(self, tag):
        if tag == "script":
            self.capture = False

    def handle_data(self, data):
        if self.capture:
            self.parts.append(data)


def song_metadata(url, downloader):
    parsed = urlsplit(validate_url(url, source=False))
    if parsed.hostname == "spotify.link":
        with downloader.urlopen(url) as response:
            parsed = urlsplit(validate_url(response.url, source=False))
        if parsed.hostname != "open.spotify.com":
            raise ValueError("This Spotify short link did not lead to a song. Copy its full open.spotify.com track link.")
    if parsed.hostname == "open.spotify.com":
        match = re.fullmatch(r"/(?:intl-[a-z]+/)?track/([A-Za-z0-9]{22})/?", parsed.path)
        if not match:
            raise ValueError("Use a Spotify link to one song, with /track/ in its address.")
        with downloader.urlopen("https://open.spotify.com/embed/track/" + match[1]) as response:
            parser = MetadataParser()
            parser.feed(response.read(4 * 1024 * 1024).decode())
        try:
            entity = json.loads("".join(parser.parts))["props"]["pageProps"]["state"]["data"]["entity"]
            title = entity["title"]
            artist = entity.get("subtitle") or ", ".join(a["name"] for a in entity.get("artists", []))
            if not title or not artist:
                raise KeyError("metadata")
            return {"title": title, "artist": artist, "provider": "Spotify"}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Spotify did not return this song's details. Try the artist's public video or audio link.") from exc
    if parsed.hostname == "music.apple.com":
        identifier = parse_qs(parsed.query).get("i", [None])[0]
        if not identifier and "/song/" in parsed.path:
            identifier = parsed.path.rstrip("/").split("/")[-1]
        country = parsed.path.strip("/").split("/")[0]
        if not identifier or not identifier.isdigit() or not re.fullmatch("[a-z]{2}", country):
            raise ValueError("Use an Apple Music song link, not an album or playlist.")
        with downloader.urlopen(f"https://itunes.apple.com/lookup?id={identifier}&country={country}") as response:
            data = json.loads(response.read(1024 * 1024))
        track = next((x for x in data.get("results", []) if str(x.get("trackId")) == identifier), None)
        if not track:
            raise ValueError("Apple Music did not return this song's details.")
        return {"title": track["trackName"], "artist": track["artistName"], "provider": "Apple Music"}
    raise ValueError("Song lookup supports open.spotify.com and music.apple.com song links.")


def lookup(url):
    install_network_guard()
    with PublicYoutubeDL({"quiet": True, "logger": QuietLogger(), "proxy": "", "socket_timeout": 20,
                          "extract_flat": True, "skip_download": True, "cachedir": False}) as downloader:
        song = song_metadata(url, downloader)
        result = downloader.extract_info(f"ytsearch5:{song['artist']} {song['title']} official audio", download=False)
        candidates = []
        for entry in (result or {}).get("entries", []):
            identifier = entry.get("id", "")
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier):
                candidates.append({"title": entry.get("title", "Untitled"), "artist": entry.get("channel") or entry.get("uploader"),
                                   "duration": entry.get("duration"), "url": "https://www.youtube.com/watch?v=" + identifier})
        if not candidates:
            raise ValueError("No public matches were available. Paste a public link from the artist instead.")
        return {**song, "candidates": candidates}


if __name__ == "__main__":
    try:
        print(json.dumps(lookup(sys.argv[1])))
    except Exception as exc:
        print(json.dumps({"error": friendly_error(exc)}))
        sys.exit(1)
