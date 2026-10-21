#!/usr/bin/env python3
"""
extradl: searches and downloads albums from Bandcamp and SoundCloud for the Lidarr plugin, next to SomeDL.

Small HTTP API (JSON), used by the plugin's "Bandcamp/SoundCloud" indexer and download client:

  GET    /api/version
  GET    /api/search?artist=..&album=..&sources=bandcamp,soundcloud   albums (and single tracks) found
  GET    /api/details?url=..                                          track count and length of one hit
  POST   /api/download   {"url", "artist", "album", "year"}          queue an album, returns {"id"}
  GET    /api/jobs                                                    every job with its progress
  DELETE /api/jobs/<id>?delete_files=1                                forget a job (and delete its files)

Downloads go to <output>/<job id>/<Artist> - <Album>/<NN> - <Title>.<ext> in the format the site serves
(no re-encoding), tagged (album, album artist, artist, title, track number, year, cover), with the
.lidarr-somedl marker so somedl_mover.py leaves them to Lidarr. Jobs survive a restart.

  extradl.py --output /data/somedl/extradl --port 5050

Needs yt-dlp and mutagen (both come with SomeDL) and ffmpeg.
"""
import argparse
import json
import os
import queue
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mutagen
import yt_dlp

VERSION = "1"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MARKER = ".lidarr-somedl"
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".opus", ".ogg", ".flac", ".aac", ".wav"}


def http_json(url, data=None):
    request = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                     headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def ydl(extra=None):
    options = {"quiet": True, "no_warnings": True, "noprogress": True}
    options.update(extra or {})
    return yt_dlp.YoutubeDL(options)


# --- search -----------------------------------------------------------------------------------

def search_bandcamp(query):
    data = http_json("https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic",
                     {"search_text": query, "search_filter": "", "full_page": False, "fan_id": None})
    results = []
    for hit in data.get("auto", {}).get("results", []):
        if hit.get("type") not in ("a", "t") or not hit.get("item_url_path"):
            continue
        results.append({
            "source": "bandcamp",
            "type": "album" if hit["type"] == "a" else "single",
            "url": hit["item_url_path"],
            "title": hit.get("name"),
            "artist": hit.get("band_name"),
            "year": None,
            "track_count": None if hit["type"] == "a" else 1,
            "duration_seconds": None,
        })
    return results


SOUNDCLOUD_CLIENT = {"id": None, "at": 0}


def soundcloud_client_id(refresh=False):
    """SoundCloud's public web client id, found the way yt-dlp finds it; refreshed after a day or a 401."""
    if refresh or not SOUNDCLOUD_CLIENT["id"] or time.time() - SOUNDCLOUD_CLIENT["at"] > 86400:
        from yt_dlp.extractor.soundcloud import SoundcloudIE
        extractor = SoundcloudIE(ydl())
        extractor._update_client_id()
        SOUNDCLOUD_CLIENT.update(id=extractor._CLIENT_ID, at=time.time())
    return SOUNDCLOUD_CLIENT["id"]


def search_soundcloud(query):
    results = []
    for kind in ("albums", "tracks"):
        for attempt in (0, 1):
            url = "https://api-v2.soundcloud.com/search/%s?%s" % (kind, urllib.parse.urlencode(
                {"q": query, "client_id": soundcloud_client_id(refresh=attempt == 1), "limit": 10}))
            try:
                data = http_json(url)
                break
            except urllib.error.HTTPError as e:
                if e.code != 401 or attempt:
                    raise
        for hit in data.get("collection", []):
            if kind == "albums" and hit.get("set_type") not in ("album", "ep", "single", "compilation"):
                continue  # user playlists are not releases
            date = hit.get("release_date") or hit.get("display_date") or hit.get("created_at") or ""
            results.append({
                "source": "soundcloud",
                "type": "single" if kind == "tracks" else (hit.get("set_type") or "album"),
                "url": hit.get("permalink_url"),
                "title": hit.get("title"),
                "artist": (hit.get("publisher_metadata") or {}).get("artist") or (hit.get("user") or {}).get("username"),
                "year": int(date[:4]) if date[:4].isdigit() else None,
                "track_count": hit.get("track_count") if kind == "albums" else 1,
                "duration_seconds": round((hit.get("duration") or 0) / 1000) or None,
            })
    return results


SEARCHERS = {"bandcamp": search_bandcamp, "soundcloud": search_soundcloud}


def details(url):
    """Track count, length and year of one album or track; search hits do not always have them."""
    with ydl({"skip_download": True}) as y:
        info = y.extract_info(url, download=False)
    entries = [e for e in (info.get("entries") or [info]) if e]
    first = entries[0] if entries else {}
    year = (info.get("release_year") or first.get("release_year")
            or str(info.get("release_date") or first.get("release_date") or first.get("upload_date") or "")[:4] or None)
    return {
        "track_count": len(entries),
        "duration_seconds": round(sum(e.get("duration") or 0 for e in entries)) or None,
        "year": int(year) if str(year or "").isdigit() else None,
    }


# --- downloads --------------------------------------------------------------------------------

class Jobs:
    def __init__(self, output):
        self.output = output
        self.path = os.path.join(output, "jobs.json")
        self.lock = threading.Lock()
        self.queue = queue.Queue()
        self.jobs = {}
        os.makedirs(output, exist_ok=True)
        try:
            with open(self.path, encoding="utf-8") as f:
                self.jobs = json.load(f)
        except (OSError, ValueError):
            self.jobs = {}
        for job in self.jobs.values():  # interrupted by a restart: start over
            if job["state"] in ("queued", "downloading"):
                job.update(state="queued", done=0)
                self.queue.put(job["id"])
        threading.Thread(target=self.worker, daemon=True).start()

    def save(self):
        temp = self.path + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(self.jobs, f)
        os.replace(temp, self.path)

    def add(self, url, artist, album, year):
        job_id = uuid.uuid4().hex[:12]
        with self.lock:
            self.jobs[job_id] = {"id": job_id, "url": url, "artist": artist, "album": album, "year": year,
                                 "state": "queued", "done": 0, "total": 0, "path": None, "error": None,
                                 "added": time.time()}
            self.save()
        self.queue.put(job_id)
        return job_id

    def update(self, job_id, **fields):
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id].update(fields)
                self.save()

    def remove(self, job_id, delete_files):
        with self.lock:
            job = self.jobs.pop(job_id, None)
            self.save()
        if job and delete_files:
            shutil.rmtree(os.path.join(self.output, job_id), ignore_errors=True)
        return job is not None

    def worker(self):
        while True:
            job_id = self.queue.get()
            job = self.jobs.get(job_id)
            if not job or job["state"] != "queued":
                continue
            try:
                self.download(job)
            except Exception as e:  # one bad album must not stop the queue
                self.update(job_id, state="failed", error=str(e)[:500])

    def download(self, job):
        folder = os.path.join(self.output, job["id"], safe_name(f"{job['artist']} - {job['album']}"))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, MARKER), "w") as f:
            f.write("Downloaded by extradl for Lidarr; somedl_mover.py leaves this folder alone.\n")
        self.update(job["id"], state="downloading", path=folder)

        def progress(status):
            if status.get("status") == "finished":
                done = sum(1 for n in os.listdir(folder) if os.path.splitext(n)[1].lower() in AUDIO_EXTENSIONS)
                self.update(job["id"], done=done)

        options = {
            "format": "bestaudio/best",
            "outtmpl": {"default": os.path.join(folder, "%(playlist_index&{0:02d}|01)s - %(track,title)s.%(ext)s"),
                        "pl_thumbnail": os.path.join(folder, "cover.%(ext)s")},
            "writethumbnail": True,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "best"},  # audio only, never re-encoded
                {"key": "EmbedThumbnail"},
            ],
            "progress_hooks": [progress],
            "ignoreerrors": False,
            "retries": 5,
        }
        with ydl(options) as y:
            info = y.extract_info(job["url"], download=False)
            entries = [e for e in (info.get("entries") or [info]) if e]
            self.update(job["id"], total=len(entries))
            y.process_ie_result(info, download=True)

        files = sorted(n for n in os.listdir(folder) if os.path.splitext(n)[1].lower() in AUDIO_EXTENSIONS)
        if not files:
            raise RuntimeError("yt-dlp downloaded nothing")
        for index, name in enumerate(files, 1):
            entry = entries[index - 1] if index <= len(entries) else {}
            tag(os.path.join(folder, name), job, entry, index, len(files))
        self.update(job["id"], state="completed", done=len(files), total=max(len(files), job.get("total") or 0))


def safe_name(text):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(text or "")).strip(" .")[:150] or "_"


def tag(path, job, entry, number, total):
    """Album tags as Lidarr matches them: the album and album artist it searched for, so every track lands
    in the same release; the per-track artist and title as the site has them."""
    audio = mutagen.File(path, easy=True)
    if audio is None:
        return
    if audio.tags is None:
        audio.add_tags()
    values = {
        "album": job["album"],
        "albumartist": job["artist"],
        "artist": entry.get("artist") or entry.get("uploader") or job["artist"],
        "title": entry.get("track") or entry.get("title") or os.path.splitext(os.path.basename(path))[0],
        "tracknumber": f"{number}/{total}",
    }
    if job.get("year"):
        values["date"] = str(job["year"])
    for key, value in values.items():
        try:
            audio[key] = [str(value)]
        except (KeyError, ValueError):
            pass  # a key this format's easy tags do not support
    audio.save()


# --- HTTP -------------------------------------------------------------------------------------

def make_handler(jobs):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            try:
                if url.path == "/api/version":
                    return self.reply(200, {"v": VERSION, "yt_dlp": yt_dlp.version.__version__})
                if url.path == "/api/search":
                    text = f"{query.get('artist', '')} {query.get('album', '')}".strip()
                    if not text:
                        return self.reply(400, {"error": "artist or album required"})
                    results, errors = [], {}
                    for source in query.get("sources", "bandcamp,soundcloud").split(","):
                        if source in SEARCHERS:
                            try:
                                results += SEARCHERS[source](text)
                            except Exception as e:  # one site down must not hide the other
                                errors[source] = str(e)[:200]
                    return self.reply(200, {"results": results, "errors": errors})
                if url.path == "/api/details":
                    return self.reply(200, details(query["url"]))
                if url.path == "/api/jobs":
                    return self.reply(200, {"jobs": list(jobs.jobs.values())})
                return self.reply(404, {"error": "not found"})
            except Exception as e:
                return self.reply(500, {"error": str(e)[:500]})

        def do_POST(self):
            if self.path != "/api/download":
                return self.reply(404, {"error": "not found"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if not body.get("url") or not body.get("album") or not body.get("artist"):
                    return self.reply(400, {"error": "url, artist and album required"})
                return self.reply(200, {"id": jobs.add(body["url"], body["artist"], body["album"], body.get("year"))})
            except Exception as e:
                return self.reply(500, {"error": str(e)[:500]})

        def do_DELETE(self):
            url = urllib.parse.urlparse(self.path)
            match = re.fullmatch(r"/api/jobs/([0-9a-f]+)", url.path)
            if not match:
                return self.reply(404, {"error": "not found"})
            delete = urllib.parse.parse_qs(url.query).get("delete_files", ["0"])[0] == "1"
            return self.reply(200, {"removed": jobs.remove(match.group(1), delete)})

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", default="/data/somedl/extradl", help="where downloads go (Lidarr must see this path)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5050)
    a = ap.parse_args()

    server = ThreadingHTTPServer((a.host, a.port), make_handler(Jobs(a.output)))
    print(f"extradl {VERSION} (yt-dlp {yt_dlp.version.__version__}) on {a.host}:{a.port}, saving to {a.output}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
