#!/usr/bin/env python3
"""Turns a Navidrome playlist of music you do not like into Lidarr actions.

Add songs, albums or artists to the playlist (default "Niet leuk") from any Subsonic app; most apps can
add to a playlist even when they cannot rate. Then, on every run:

  - an album is disliked when all of its songs are in the playlist (adding an album adds all of them);
    Lidarr stops monitoring it, so it is never searched for or upgraded again, or with --mode delete
    removes it with its files and adds it to the import list exclusions, so no list adds it back
  - an artist is disliked when all of its albums in Navidrome are; Lidarr stops monitoring the artist,
    so new releases are not added either (the artist's other albums in Lidarr stay as they are)
  - single songs only show up in the report: Lidarr manages whole albums, and deleting one track would
    make it fetch the album again

Albums are matched on the MusicBrainz release id Lidarr writes into the tags, else on artist and title.
Needs Python 3.8+ (standard library only).

    navidrome_dislikes.py --dry-run          shows what would happen
    navidrome_dislikes.py                    unmonitors
    navidrome_dislikes.py --mode delete      deletes

Settings come from the environment (the installer keeps them in /etc/navidrome-dislikes.env):
    NAVIDROME_URL, NAVIDROME_USER, NAVIDROME_PASSWORD, DISLIKE_PLAYLIST (default "Niet leuk"),
    LIDARR_URL (default http://127.0.0.1:8686), LIDARR_API_KEY (default: read from Lidarr's config.xml)
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import unicodedata
import urllib.parse
import urllib.request


def log(message):
    print(message, flush=True)


def http_json(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def normalize(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*$", "", text)  # "(Deluxe Edition)" and the like
    return re.sub(r"[^a-z0-9]", "", re.sub(r"^the\s+", "", text))


class Navidrome:
    def __init__(self, url, user, password):
        self.base = url.rstrip("/") + "/rest/"
        self.user = user
        self.password = password

    def call(self, endpoint, **params):
        salt = secrets.token_hex(6)
        query = {"u": self.user, "t": hashlib.md5((self.password + salt).encode()).hexdigest(), "s": salt,
                 "v": "1.16.1", "c": "navidrome-dislikes", "f": "json", **params}
        answer = http_json(self.base + endpoint + "?" + urllib.parse.urlencode(query))["subsonic-response"]
        if answer.get("status") != "ok":
            raise RuntimeError(f"Navidrome {endpoint}: {answer.get('error', {}).get('message', answer)}")
        return answer

    def playlist_songs(self, name):
        playlists = self.call("getPlaylists").get("playlists", {}).get("playlist", [])
        match = [p for p in playlists if p.get("name", "").strip().lower() == name.strip().lower()]
        if not match:
            return None
        return self.call("getPlaylist", id=match[0]["id"]).get("playlist", {}).get("entry", [])

    def album(self, album_id):
        return self.call("getAlbum", id=album_id).get("album", {})

    def artist(self, artist_id):
        return self.call("getArtist", id=artist_id).get("artist", {})


class Lidarr:
    def __init__(self, url, api_key):
        self.base = url.rstrip("/") + "/api/v1/"
        self.headers = {"X-Api-Key": api_key}

    def call(self, path, method="GET", body=None):
        return http_json(self.base + path, method, body, self.headers)


def lidarr_api_key():
    key = os.environ.get("LIDARR_API_KEY")
    if key:
        return key
    for path in ("/var/lib/lidarr/config.xml", "/config/config.xml"):
        try:
            found = re.search(r"<ApiKey>([^<]+)</ApiKey>", open(path).read())
        except OSError:
            continue
        if found:
            return found.group(1)
    sys.exit("No Lidarr API key: set LIDARR_API_KEY")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("unmonitor", "delete"), default=os.environ.get("DISLIKE_MODE", "unmonitor"))
    parser.add_argument("--dry-run", action="store_true", help="only show what would happen")
    parser.add_argument("--env-file", help="KEY=value lines, read literally (passwords may hold any character)")
    args = parser.parse_args()
    if args.env_file:
        for line in open(args.env_file, encoding="utf-8"):
            key, sep, value = line.rstrip("\n").partition("=")
            if sep and key.strip() and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value)
        if "DISLIKE_MODE" in os.environ and "--mode" not in sys.argv:
            args.mode = os.environ["DISLIKE_MODE"]

    for name in ("NAVIDROME_URL", "NAVIDROME_USER", "NAVIDROME_PASSWORD"):
        if not os.environ.get(name):
            sys.exit(f"{name} is not set")
    playlist = os.environ.get("DISLIKE_PLAYLIST", "Niet leuk")
    navidrome = Navidrome(os.environ["NAVIDROME_URL"], os.environ["NAVIDROME_USER"], os.environ["NAVIDROME_PASSWORD"])
    lidarr = Lidarr(os.environ.get("LIDARR_URL", "http://127.0.0.1:8686"), lidarr_api_key())
    prefix = "[dry run] " if args.dry_run else ""

    songs = navidrome.playlist_songs(playlist)
    if songs is None:
        log(f"No playlist '{playlist}' for {os.environ['NAVIDROME_USER']} in Navidrome; nothing to do")
        return 0
    if not songs:
        log(f"'{playlist}' is empty; nothing to do")
        return 0
    disliked_ids = {s["id"] for s in songs}

    # Which Navidrome albums are disliked as a whole
    albums, singles = {}, []
    for album_id in sorted({s.get("albumId") for s in songs if s.get("albumId")}):
        try:
            album = navidrome.album(album_id)
        except Exception:  # gone from Navidrome, e.g. deleted on an earlier run
            continue
        album_songs = {s["id"] for s in album.get("song", [])}
        if album_songs and album_songs <= disliked_ids:
            albums[album_id] = album
        else:
            singles += [s for s in songs if s.get("albumId") == album_id]

    # Lidarr's albums by MusicBrainz release id, and by artist + title as a fallback
    lidarr_albums = lidarr.call("album")
    by_release, by_name = {}, {}
    for album in lidarr_albums:
        for release in album.get("releases") or []:
            by_release[release.get("foreignReleaseId")] = album
        artist_name = (album.get("artist") or {}).get("artistName")
        by_name[(normalize(artist_name), normalize(album.get("title")))] = album

    to_change, missing = [], []
    for album in albums.values():
        target = by_release.get(album.get("musicBrainzId")) \
            or by_name.get((normalize(album.get("artist")), normalize(album.get("name"))))
        label = f"{album.get('artist')} - {album.get('name')}"
        if target is None:
            missing.append(label)
        elif args.mode == "delete" or target.get("monitored"):
            to_change.append((label, target))

    for label, target in to_change:
        if args.mode == "delete":
            log(f"{prefix}album: {label}: deleting it and its files from Lidarr (and excluding it from import lists)")
            if not args.dry_run:
                lidarr.call(f"album/{target['id']}?deleteFiles=true&addImportListExclusion=true", "DELETE")
        else:
            log(f"{prefix}album: {label}: no longer monitored in Lidarr")
    if args.mode == "unmonitor" and to_change and not args.dry_run:
        lidarr.call("album/monitor", "PUT", {"albumIds": [t["id"] for _, t in to_change], "monitored": False})

    # Artists all of whose Navidrome albums are disliked
    for artist_id in sorted({a.get("artistId") for a in albums.values() if a.get("artistId")}):
        artist = navidrome.artist(artist_id)
        artist_albums = {a["id"] for a in artist.get("album", [])}
        if not artist_albums or not artist_albums <= set(albums):
            continue
        lidarr_artist = next((a for a in lidarr.call("artist")
                              if a.get("foreignArtistId") == artist.get("musicBrainzId")
                              or normalize(a.get("artistName")) == normalize(artist.get("name"))), None)
        if lidarr_artist is None or not lidarr_artist.get("monitored"):
            continue
        log(f"{prefix}artist: {artist.get('name')}: no longer monitored in Lidarr, so new releases are not added")
        if not args.dry_run:
            lidarr_artist["monitored"] = False
            lidarr_artist["monitorNewItems"] = "none"
            lidarr.call(f"artist/{lidarr_artist['id']}", "PUT", lidarr_artist)

    for label in missing:
        log(f"album: {label}: not in Lidarr (downloaded outside it?), left alone")
    for song in singles:
        log(f"song: {song.get('artist')} - {song.get('title')} ({song.get('album')}): only this song, the album stays")
    if not to_change and not missing:
        log(f"'{playlist}': {len(songs)} song(s), nothing new for Lidarr")
    return 0


if __name__ == "__main__":
    sys.exit(main())
