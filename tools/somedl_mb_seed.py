#!/usr/bin/env python3
"""
Add a YouTube Music album that MusicBrainz (and therefore Lidarr) does not know yet.

Looks the album up through SomeDL, then writes an HTML page that opens MusicBrainz' "Add release"
editor with everything filled in: title, type, date, artist, tracklist with lengths and the YouTube
Music link. You check it and press "Enter edit" there; nothing is submitted for you. You need a
(free) MusicBrainz account.

Before seeding it asks MusicBrainz whether the artist already exists (so the release is linked to
it instead of creating a duplicate artist) and warns when a release with this name seems to be there.

Usage:
  somedl_mb_seed.py --somedl http://somedl:5000 "artist album"     search, then pick a hit
  somedl_mb_seed.py --somedl http://somedl:5000 --album MPREb_xxxx  album browse id from a music.youtube.com/browse/... link
  somedl_mb_seed.py ... "https://music.youtube.com/playlist?list=OLAK5uy_..."   or a YouTube (Music) link:
                    album (browse/playlist) or a song/video (watch?v=, youtu.be/), whose album is used
  somedl_mb_seed.py ... --serve                                     serve the page on this machine, open it from any device
  somedl_mb_seed.py ... --open                                      also open the page in your browser
  somedl_mb_seed.py ... --download                                  also let SomeDL download the album now
  somedl_mb_seed.py ... --library /data/music [--link]              download, wait, then move (or hardlink) it into your library

Once MusicBrainz has it, Lidarr's metadata server picks it up within a day or so; then use
"Refresh & Scan" on the artist in Lidarr (or add the artist if it is new).

Standard library only. Seeding fields: https://wiki.musicbrainz.org/Development/Release_Editor_Seeding
"""
import argparse
import html
import http.server
import re
import socket
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

MB = "https://musicbrainz.org"
USER_AGENT = "Lidarr.Plugin.SomeDL-mb-seed/0.1 ( https://github.com/ddylvianen/lidarr-someDL-plugin )"
RELEASE_TYPES = {"album": "album", "ep": "ep", "single": "single"}


def somedl(base, path, payload=None):
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def musicbrainz(path, **query):
    # MusicBrainz allows one request per second and answers 503 above that
    query["fmt"] = "json"
    req = urllib.request.Request(f"{MB}/ws/2/{path}?{urllib.parse.urlencode(query)}", headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        time.sleep(1.1 * (attempt + 1))
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 503:
                error = e
                break
            error = e
        except OSError as e:
            error = e
            break
    print(f"  (MusicBrainz lookup failed: {error}; continuing without it)")
    return None


def lucene(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def pick_album(base, query):
    hits = [h for h in somedl(base, "/yt-search", {"search_query": query, "filter": "albums"})["result"] if h.get("browseId")]
    if not hits:
        sys.exit("No album found on YouTube Music for: " + query)
    for i, h in enumerate(hits[:10]):
        artists = ", ".join(a["name"] for a in h.get("artists", []))
        print(f"[{i}] {artists} - {h['title']} ({h.get('type')}, {h.get('year')})")
    n = input("Pick a number [0]: ").strip() or "0"
    return hits[int(n)]["browseId"]


def resolve_link(base, link):
    """Album browse id (MPREb_...) for a YouTube or YouTube Music link, or None when it is not a link."""
    if not re.match(r"^https?://", link.strip()):
        return None
    url = urllib.parse.urlparse(link.strip())
    params = urllib.parse.parse_qs(url.query)

    browse = re.search(r"/browse/(MPRE[\w-]+)", url.path)
    if browse:
        return browse.group(1)

    playlist = (params.get("list") or [""])[0]
    if playlist.startswith("OLAK5uy_"):
        # An album's audio playlist; its tracks name the album they belong to
        tracks = somedl(base, "/yt-get-album", {"album_id": playlist})["result"].get("tracks") or []
        album_id = next((t["album"]["id"] for t in tracks if (t.get("album") or {}).get("id")), None)
        if album_id:
            return album_id
        sys.exit("That playlist has no album on YouTube Music.")
    if playlist and "v" not in params:
        sys.exit("That is a user playlist, not an album. Give an album link, or a song from the album.")

    video = (params.get("v") or [""])[0] or (url.path.strip("/") if url.netloc.endswith("youtu.be") else "")
    if not video:
        sys.exit("Could not find an album, playlist or video in that link.")
    return album_for_video(base, video)


def album_for_video(base, video_id):
    """The YouTube Music album of a song. Music videos are not on an album, so they are matched to the
    song with the same title and artist; when that is ambiguous, you pick."""
    try:
        with urllib.request.urlopen("https://www.youtube.com/oembed?format=json&url=" +
                                    urllib.parse.quote(f"https://www.youtube.com/watch?v={video_id}"), timeout=30) as r:
            info = json.load(r)
    except OSError as e:
        sys.exit(f"Could not look up that video on YouTube ({e}).")
    artist = re.sub(r"\s+-\s+Topic$", "", info.get("author_name", ""))
    title = info.get("title", "")
    print(f"Video: {artist} - {title}")

    songs = somedl(base, "/yt-search", {"search_query": f"{artist} {title}", "filter": "songs"})["result"]
    songs = [x for x in songs if (x.get("album") or {}).get("id")]
    exact = [x for x in songs if x.get("videoId") == video_id]
    if exact:
        return exact[0]["album"]["id"]

    albums = []
    for x in songs:
        if x["album"]["id"] not in [a["album"]["id"] for a in albums]:
            albums.append(x)
    if not albums:
        sys.exit("This video is not linked to an album on YouTube Music. Search by name instead.")
    for i, x in enumerate(albums[:8]):
        print(f"[{i}] {', '.join(a['name'] for a in x.get('artists', []))} - {x['title']}  (album: {x['album']['name']})")
    n = input("Which album? [0]: ").strip() or "0"
    return albums[int(n)]["album"]["id"]


def serve(path, port):
    """Serves the page once on this machine, so a browser elsewhere (PC, phone) can open it."""
    with open(path, "rb") as f:
        body = f.read()

    class Handler(http.server.BaseHTTPRequestHandler):
        served = False

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            Handler.served = True

        def log_message(self, *args):
            pass

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))  # no packet is sent; only picks the outgoing interface
        ip = probe.getsockname()[0]
        probe.close()
    except OSError:
        ip = socket.gethostname()

    server = http.server.HTTPServer(("0.0.0.0", port), Handler)
    print(f"Open http://{ip}:{port}/ in a browser where you are logged in to MusicBrainz (Ctrl+C to stop).", flush=True)
    try:
        while not Handler.served:
            server.handle_request()
        # The form posts straight to MusicBrainz, so one visit is all it needs; linger briefly for a reload
        server.timeout = 30
        server.handle_request()
    except KeyboardInterrupt:
        pass
    server.server_close()


def find_artist(name):
    """MBID of the artist when MusicBrainz has exactly one exact-name match, else None."""
    data = musicbrainz("artist/", query=f"artist:{lucene(name)}", limit=10)
    if not data:
        return None
    exact = [a for a in data.get("artists", []) if a["name"].casefold() == name.casefold()]
    if len(exact) == 1:
        a = exact[0]
        print(f"  Artist on MusicBrainz: {a['name']} ({a.get('disambiguation') or a.get('country') or 'no details'}) {MB}/artist/{a['id']}")
        return a["id"]
    if exact:
        print(f"  {len(exact)} artists named '{name}' on MusicBrainz; pick the right one in the editor:")
        for a in exact:
            print(f"    {MB}/artist/{a['id']}  {a.get('disambiguation') or a.get('country') or ''}")
    else:
        print(f"  '{name}' is not on MusicBrainz yet; the editor will let you add the artist too.")
    return None


def warn_if_exists(artist, title):
    data = musicbrainz("release/", query=f"release:{lucene(title)} AND artist:{lucene(artist)}", limit=5)
    found = [r for r in (data or {}).get("releases", []) if r.get("score", 0) >= 90]
    for r in found:
        print(f"  WARNING: MusicBrainz may already have this: {r['title']} ({r.get('date', '?')}) {MB}/release/{r['id']}")
    return bool(found)


def credit(prefix, names, mbids=None):
    fields = []
    for i, name in enumerate(names):
        fields.append((f"{prefix}.names.{i}.name", name))
        fields.append((f"{prefix}.names.{i}.artist.name", name))
        if mbids and mbids.get(name):
            fields.append((f"{prefix}.names.{i}.mbid", mbids[name]))
        if i < len(names) - 1:
            fields.append((f"{prefix}.names.{i}.join_phrase", " & " if i == len(names) - 2 else ", "))
    return fields


def build_fields(album, mbids, browse_id):
    artists = [a["name"] for a in album.get("artists") or [] if a.get("name")]
    fields = [
        ("name", album.get("title", "")),
        ("status", "official"),
        ("mediums.0.format", "Digital Media"),
        ("edit_note", "Seeded from YouTube Music via SomeDL: "
                      f"https://music.youtube.com/browse/{browse_id}\nPlease check against the source."),
    ]
    release_type = RELEASE_TYPES.get((album.get("type") or "").lower())
    if release_type:
        fields.append(("type", release_type))
    if str(album.get("year") or "").isdigit():
        fields.append(("events.0.date.year", str(album["year"])))
    fields += credit("artist_credit", artists, mbids)
    # The relationship type is left for the editor to pick ("stream for free" / "streaming page");
    # it recognises music.youtube.com links and suggests one.
    if album.get("audioPlaylistId"):
        fields.append(("urls.0.url", f"https://music.youtube.com/playlist?list={album['audioPlaylistId']}"))

    for i, t in enumerate(album.get("tracks") or []):
        fields.append((f"mediums.0.track.{i}.name", t.get("title", "")))
        fields.append((f"mediums.0.track.{i}.number", str(t.get("trackNumber") or i + 1)))
        if t.get("duration_seconds"):
            fields.append((f"mediums.0.track.{i}.length", str(int(t["duration_seconds"]) * 1000)))
        names = [a["name"] for a in t.get("artists") or [] if a.get("name")]
        if names and names != artists:
            fields += credit(f"mediums.0.track.{i}.artist_credit", names, mbids)
    return fields


def render(fields, title):
    inputs = "\n".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in fields)
    rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>" for k, v in fields)
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>MusicBrainz seed: {html.escape(title)}</title>
<body style="font-family:system-ui,sans-serif;max-width:48rem;margin:2rem auto;padding:0 1rem">
<h1>{html.escape(title)}</h1>
<p>Opens the MusicBrainz release editor pre-filled. Check everything there, then press <b>Enter edit</b>.</p>
<form method="post" action="{MB}/release/add" target="_blank" accept-charset="UTF-8">
{inputs}
<button style="font-size:1.1rem;padding:.6rem 1.2rem">Open in MusicBrainz</button>
</form>
<details style="margin-top:2rem"><summary>Fields being sent</summary>
<table style="font-size:.85rem;border-collapse:collapse">{rows}</table></details>
</body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", help="search text, e.g. 'artist album', or a YouTube / YouTube Music link")
    ap.add_argument("--somedl", default=os.environ.get("SOMEDL_URL", "http://localhost:5000"), help="SomeDL WebUI URL (or set SOMEDL_URL)")
    ap.add_argument("--album", help="YouTube Music album browse id (MPREb_...) instead of searching")
    ap.add_argument("--from-json", help="a saved /yt-get-album-browse-id reply, for offline use")
    ap.add_argument("--artist-mbid", help="MusicBrainz id of the release artist, skips the lookup")
    ap.add_argument("-o", "--out", help="output file (default: musicbrainz_seed_<album>.html)")
    ap.add_argument("--open", action="store_true", help="open the page in your browser")
    ap.add_argument("--serve", nargs="?", const=8099, type=int, metavar="PORT",
                    help="serve the page on this machine (default port 8099) so you can open it from another device")
    ap.add_argument("--download", action="store_true",
                    help="also queue the album in SomeDL now, so you can listen before Lidarr knows it")
    ap.add_argument("--library", metavar="DIR",
                    help="download (implies --download), wait until SomeDL is done, then move the album into DIR. "
                         "Run this on the machine where SomeDL saves its files")
    ap.add_argument("--link", action="store_true",
                    help="with --library: hardlink instead of move, so the files stay in SomeDL's folder too")
    a = ap.parse_args()

    if a.from_json:
        with open(a.from_json, encoding="utf-8") as f:
            data = json.load(f)
        album, browse_id = data.get("album_data", data), a.album or "unknown"
    else:
        browse_id = a.album or (a.query and (resolve_link(a.somedl, a.query) or pick_album(a.somedl, a.query)))
        if not browse_id:
            ap.error("give a search query or --album")
        album = somedl(a.somedl, "/yt-get-album-browse-id", {"album_id": browse_id, "return_album_data": True})["album_data"]

    artists = [x["name"] for x in album.get("artists") or [] if x.get("name")]
    print(f"{', '.join(artists)} - {album.get('title')} ({album.get('type')}, {album.get('year')}, {len(album.get('tracks') or [])} tracks)")

    mbids = {}
    if artists:
        mbids[artists[0]] = a.artist_mbid or find_artist(artists[0])
        warn_if_exists(artists[0], album.get("title", ""))

    fields = build_fields(album, mbids, browse_id)
    safe = "".join(c if c.isalnum() else "_" for c in album.get("title", "release"))[:60]
    out = a.out or f"musicbrainz_seed_{safe}.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(fields, f"{', '.join(artists)} - {album.get('title', '')}"))
    print(f"Wrote {out}. Open it in a browser where you are logged in to MusicBrainz.")
    if a.open:
        webbrowser.open("file://" + os.path.abspath(out))
    if a.serve:
        serve(out, a.serve)
    if a.download or a.library:
        ids = download(a.somedl, album)
        if a.library and ids:
            to_library(a.somedl, ids, a.library, a.link)


def download(base, album):
    # Same call as SomeDL's own album button, so the tracks are tagged with this album and land in
    # its album folder. Once Lidarr knows the album, its search through the SomeDL plugin finds the
    # files already there (SomeDL reports them as already downloaded) and imports them.
    playlist = album.get("audioPlaylistId")
    if not playlist:
        print("No playlist id for this album; download it from the SomeDL WebUI instead.")
        return
    songs = somedl(base, "/yt-download", {"url": f"https://music.youtube.com/playlist?list={playlist}"}).get("song_list", [])
    print(f"SomeDL queued {len(songs)} tracks; follow them in its WebUI ({base}).")
    return [s["somedl_id"] for s in songs if s.get("somedl_id")]


def wait_for(base, ids):
    """somedl_id -> [state, path] once every track has finished."""
    pending = set(ids)
    finished = {}
    while pending:
        time.sleep(5)
        status = somedl(base, "/status")
        for i in list(pending):
            if i in status.get("finished_items", {}):
                finished[i] = status["finished_items"][i]
                pending.discard(i)
        print(f"\r  {len(finished)}/{len(ids)} tracks done", end="", flush=True)
    print()
    return finished


def to_library(base, ids, library, link):
    """
    Moves the album from SomeDL's folder into the library, keeping SomeDL's Artist/Album layout. That
    is the layout Lidarr uses too, so once Lidarr knows the album, "Rescan" on the artist adopts the
    files where they are. A hardlink is the same file under a second name: no extra space, and it
    keeps working whichever of the two is moved or deleted later (a symlink would break when Lidarr
    moves or renames its target).
    """
    root = somedl(base, "/settings-read")["config"]["download"]["output_dir"]
    if not os.path.isdir(root):
        sys.exit(f"SomeDL saves to {root}, which is not on this machine. Run this script where SomeDL runs.")

    done = 0
    folders = set()
    for state, path in wait_for(base, ids).values():
        if state not in ("success", "already_downloaded") or not path or not os.path.isfile(path):
            continue
        target = os.path.join(library, os.path.relpath(path, root))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.exists(target):
            print(f"  already in library: {target}")
            continue
        if link:
            os.link(path, target)
        else:
            shutil.move(path, target)
            folders.add(os.path.dirname(path))
        done += 1

    # Leave no empty album/artist folders behind in SomeDL's folder
    for folder in sorted(folders, key=len, reverse=True):
        while folder.startswith(root.rstrip("/") + "/") and os.path.isdir(folder) and not os.listdir(folder):
            os.rmdir(folder)
            folder = os.path.dirname(folder)

    failed = len(ids) - done
    print(f"{'Linked' if link else 'Moved'} {done} tracks into {library}" + (f"; {failed} failed or were already there" if failed else ""))


if __name__ == "__main__":
    main()
