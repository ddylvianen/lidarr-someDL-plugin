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
import subprocess
import tempfile
import json
import os
import shutil
import sys
import time
import unicodedata
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
    """For a YouTube or YouTube Music link: the album browse id (MPREb_...), or for a plain YouTube upload
    that is not on YouTube Music, the video described as a single (dict). None when it is not a link."""
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


NOISE = re.compile(r"\s*[\(\[][^\)\]]*\b(official|video|audio|lyrics?|visuali[sz]er|clip|hd|4k|hq)\b[^\)\]]*[\)\]]", re.I)


def normalize(name):
    name = re.sub(r"^the\s+", "", (name or "").strip(), flags=re.I)
    return "".join(c for c in unicodedata.normalize("NFKD", name).lower() if c.isalnum())


def similarity(a, b):
    """0..1 by edit distance on normalized names, the same rule the Lidarr indexer uses."""
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return 1 - prev[-1] / max(len(a), len(b))


def tool(name):
    """A binary from PATH, or from SomeDL's venv (where install-somedl.sh puts yt-dlp)."""
    return shutil.which(name) or (f"/opt/somedl/bin/{name}" if os.path.exists(f"/opt/somedl/bin/{name}") else None)


def video_info(video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"
    ytdlp = tool("yt-dlp")
    if ytdlp:
        try:
            out = subprocess.run([ytdlp, "-J", "--no-warnings", "--skip-download", url],
                                 capture_output=True, text=True, timeout=120, check=True).stdout
            return json.loads(out)
        except (subprocess.SubprocessError, ValueError) as e:
            print(f"  (yt-dlp could not read the video: {e}; falling back to basic info)")
    try:
        with urllib.request.urlopen("https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url), timeout=30) as r:
            info = json.load(r)
        return {"title": info.get("title"), "channel": info.get("author_name")}
    except OSError as e:
        sys.exit(f"Could not look up that video on YouTube ({e}).")


def guess_artist_title(info):
    """'Artist - Title (Official Video)' as most uploads are named; YouTube's own artist/track if it has them."""
    if info.get("artist") and info.get("track"):
        return info["artist"].split(",")[0].strip(), info["track"].strip()
    title = NOISE.sub("", info.get("title") or "").strip()
    channel = re.sub(r"\s*(-\s*Topic|VEVO)$", "", info.get("channel") or info.get("uploader") or "", flags=re.I).strip()
    if " - " in title:
        artist, title = title.split(" - ", 1)
        return artist.strip(), title.strip()
    return channel, title


def ask(label, default):
    answer = input(f"{label} [{default}]: ").strip()
    return answer or default


def album_for_video(base, video_id):
    """The YouTube Music album of a song. A music video or plain upload is matched to a YouTube Music song
    with the same title and artist (80% or more); without one, the video itself becomes a single."""
    info = video_info(video_id)
    artist, title = guess_artist_title(info)
    print(f"Video: {info.get('title')}  ->  {artist} - {title}")

    songs = somedl(base, "/yt-search", {"search_query": f"{artist} {title}", "filter": "songs"})["result"]
    songs = [x for x in songs if (x.get("album") or {}).get("id")]
    exact = [x for x in songs if x.get("videoId") == video_id]
    if exact:
        return exact[0]["album"]["id"]

    matches = []
    for x in songs:
        if similarity(title, x.get("title")) >= 0.8 and \
                any(similarity(artist, a.get("name")) >= 0.8 for a in x.get("artists", [])) and \
                x["album"]["id"] not in [m["album"]["id"] for m in matches]:
            matches.append(x)

    if matches:
        for i, x in enumerate(matches[:8]):
            print(f"[{i}] {', '.join(a['name'] for a in x.get('artists', []))} - {x['title']}  (album: {x['album']['name']})")
        print(f"[{len(matches[:8])}] none of these: use the video itself as a single")
        n = int(input("Which one? [0]: ").strip() or "0")
        if n < len(matches[:8]):
            return matches[n]["album"]["id"]
    else:
        print("Not on YouTube Music; using the video itself as a single.")

    print("Check the names (Enter keeps them); they go into the tags and the MusicBrainz form:")
    artist = ask("  Artist", artist)
    title = ask("  Title", title)
    date = info.get("release_date") or info.get("upload_date") or ""
    return {
        "title": title,
        "type": "Single",
        "year": info.get("release_year") or (date[:4] if date[:4].isdigit() else None),
        "artists": [{"name": artist}],
        "tracks": [{"title": title, "trackNumber": 1, "duration_seconds": info.get("duration"), "artists": [{"name": artist}]}],
        "video_id": video_id,
        "source_url": f"https://www.youtube.com/watch?v={video_id}",
    }


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
    source = album.get("source_url") or f"https://music.youtube.com/browse/{browse_id}"
    artists = [a["name"] for a in album.get("artists") or [] if a.get("name")]
    fields = [
        ("name", album.get("title", "")),
        ("status", "official"),
        ("mediums.0.format", "Digital Media"),
        ("edit_note", f"Seeded via SomeDL from {source}\nPlease check against the source."),
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
    elif album.get("source_url"):
        fields.append(("urls.0.url", album["source_url"]))

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
        if isinstance(browse_id, dict):
            album, browse_id = browse_id, None
        else:
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
    if (a.download or a.library) and album.get("video_id"):
        download_video(a.somedl, album, a.library)
    elif a.download or a.library:
        ids = download(a.somedl, album)
        if a.library and ids:
            to_library(a.somedl, ids, a.library, a.link)


def safe_name(text):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(text or "")).strip(" .") or "_"


def download_video(base, album, library):
    """A plain YouTube upload, which SomeDL cannot tag (it would look the song up on YouTube Music and may
    pick another one). Downloaded with yt-dlp and tagged here, into the library or SomeDL's folder, named
    with SomeDL's own output template so it sits with everything else."""
    ytdlp, ffmpeg = tool("yt-dlp"), tool("ffmpeg")
    if not ytdlp or not ffmpeg:
        sys.exit("Downloading a plain YouTube video needs yt-dlp and ffmpeg on this machine (the SomeDL LXC has both).")

    config = somedl(base, "/settings-read")["config"]["download"]
    root = library or config["output_dir"]
    if not os.path.isdir(root):
        sys.exit(f"{root} is not on this machine. Run this where SomeDL saves its files, or give --library.")

    artist, title = album["artists"][0]["name"], album["title"]
    year = str(album.get("year") or "")
    relative = config.get("output") or "{album_artist}/{album}/{track_pos} - {song}"
    relative = relative.format(artist=safe_name(artist), album_artist=safe_name(artist), song=safe_name(title),
                               album=safe_name(title), year=year, track_pos="01", track_count="1")
    target = os.path.join(root, relative + ".mp3")
    if os.path.exists(target):
        sys.exit(f"Already there: {target}")
    os.makedirs(os.path.dirname(target), exist_ok=True)

    with tempfile.TemporaryDirectory() as work:
        print("Downloading with yt-dlp ...")
        subprocess.run([ytdlp, "--no-warnings", "-q", "--no-playlist", "-f", "bestaudio/best",
                        "-x", "--audio-format", "mp3", "--audio-quality", "0",
                        "--embed-thumbnail", "--convert-thumbnails", "jpg",
                        # square cover from the 16:9 thumbnail
                        "--ppa", "ThumbnailsConvertor+ffmpeg_o:-c:v mjpeg -vf crop=\"'if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'\"",
                        "-o", os.path.join(work, "audio.%(ext)s"), album["source_url"]], check=True)
        tags = {"title": title, "artist": artist, "album_artist": artist, "album": title,
                "track": "1/1", "date": year, "comment": album["source_url"]}
        args = [ffmpeg, "-loglevel", "error", "-i", os.path.join(work, "audio.mp3"), "-map", "0", "-c", "copy",
                "-id3v2_version", "3"]
        for key, value in tags.items():
            if value:
                args += ["-metadata", f"{key}={value}"]
        subprocess.run(args + [target], check=True)
    print(f"Saved {target}")


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
