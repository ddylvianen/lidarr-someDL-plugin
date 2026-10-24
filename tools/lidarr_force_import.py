#!/usr/bin/env python3
"""Imports downloads that are done but that Lidarr's queue still shows as queued or downloading.

Tubifarry's slskd client sometimes keeps an album at "queued" although slskd finished every file; its own
per-file bookkeeping falls out of step with slskd. The files are in the download's folder all the same.
For each such queue item this asks Lidarr to import that folder as that download (the same call a
download client's "on complete" script makes), so it is imported, recorded and leaves the queue.

An item counts as done when its folder holds audio files, nothing that looks unfinished, and nothing
changed in it for --idle-minutes. Run in the Lidarr LXC:

    lidarr_force_import.py                 shows what it would import
    lidarr_force_import.py --go            imports
    lidarr_force_import.py --protocol all  any download client, not only Soulseek

LIDARR_URL (default http://127.0.0.1:8686) and LIDARR_API_KEY (default: from Lidarr's config.xml).
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request

AUDIO = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".aiff", ".alac", ".wma", ".ape", ".wv"}
UNFINISHED = re.compile(r"\.(incomplete|part|partial|tmp|!qb|crdownload)$", re.I)


def api_key():
    if os.environ.get("LIDARR_API_KEY"):
        return os.environ["LIDARR_API_KEY"]
    for path in ("/var/lib/lidarr/config.xml", "/config/config.xml"):
        try:
            found = re.search(r"<ApiKey>([^<]+)</ApiKey>", open(path).read())
        except OSError:
            continue
        if found:
            return found.group(1)
    sys.exit("No Lidarr API key: set LIDARR_API_KEY")


def call(base, key, path, body=None):
    request = urllib.request.Request(base.rstrip("/") + "/api/v1/" + path, method="POST" if body else "GET",
                                     data=json.dumps(body).encode() if body else None,
                                     headers={"X-Api-Key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read() or b"null")


def folder_state(path, idle_minutes):
    """None when the folder looks finished, else why not."""
    if not path or not os.path.isdir(path):
        return "folder not found here"
    audio, newest = 0, 0.0
    for root, _, files in os.walk(path):
        for name in files:
            if UNFINISHED.search(name):
                return f"unfinished file {name}"
            if os.path.splitext(name)[1].lower() in AUDIO:
                audio += 1
            newest = max(newest, os.path.getmtime(os.path.join(root, name)))
    if audio == 0:
        return "no audio files"
    idle = (time.time() - newest) / 60
    if idle < idle_minutes:
        return f"changed {idle:.0f} min ago"
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--go", action="store_true", help="import (without it, only show)")
    parser.add_argument("--protocol", default="soulseek", help="download protocol to look at, or 'all'")
    parser.add_argument("--idle-minutes", type=int, default=30)
    args = parser.parse_args()

    base = os.environ.get("LIDARR_URL", "http://127.0.0.1:8686")
    key = api_key()
    records = call(base, key, "queue?pageSize=500&includeUnknownArtistItems=true").get("records", [])

    done = 0
    for r in records:
        protocol = (r.get("protocol") or "").lower()
        if args.protocol != "all" and not protocol.startswith(args.protocol.lower()):
            continue
        if r.get("status") not in ("queued", "downloading") or r.get("trackedDownloadState") != "downloading":
            continue
        title = r.get("title", "")
        why_not = folder_state(r.get("outputPath"), args.idle_minutes)
        if why_not:
            print(f"skip:   {title} ({why_not})", flush=True)
            continue
        done += 1
        print(f"{'import' if args.go else 'would import'}: {title} <- {r['outputPath']}", flush=True)
        if args.go:
            call(base, key, "command", {"name": "DownloadedAlbumsScan", "path": r["outputPath"],
                                        "downloadClientId": r["downloadId"], "importMode": "auto"})
    if not done:
        print("nothing finished that is still waiting", flush=True)
    elif not args.go:
        print("run again with --go to import these", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
