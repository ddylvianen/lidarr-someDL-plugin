#!/usr/bin/env python3
"""Cancels queued search tasks in Lidarr so its task queue cannot fill up.

Lidarr runs three tasks at a time and starts them in the order they were queued, so a flood of album
searches (Search All, Search Sniper, adding an artist with "search for missing albums") holds up
everything behind it, imports and RSS sync included. This cancels queued AlbumSearch, ArtistSearch,
MissingAlbumSearch and CutoffUnmetAlbumSearch tasks, oldest first, and leaves the newest --keep of them;
nothing else is touched. RSS sync and Lidarr's own scheduled searches find those albums again later.

Lidarr's own cancel only drops a task from memory: its row stays "queued" in the database and comes back
at the next start, which is how searches from days ago keep returning. So the rows of the cancelled
tasks are deleted from lidarr.db as well (--db, default /var/lib/lidarr/lidarr.db). Lidarr no longer
holds those tasks, so it never writes to these rows again.

    lidarr_queue_cap.py                 cancels all queued searches
    lidarr_queue_cap.py --keep 50       leaves the 50 newest (for an hourly cron job)
    lidarr_queue_cap.py --dry-run       only counts

LIDARR_URL (default http://127.0.0.1:8686) and LIDARR_API_KEY (default: from Lidarr's config.xml).
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request

SEARCHES = {"AlbumSearch", "ArtistSearch", "MissingAlbumSearch", "CutoffUnmetAlbumSearch"}


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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", type=int, default=0, help="newest queued searches to leave alone")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db", default="/var/lib/lidarr/lidarr.db", help="Lidarr's SQLite database ('' to skip)")
    args = parser.parse_args()

    base = os.environ.get("LIDARR_URL", "http://127.0.0.1:8686").rstrip("/") + "/api/v1/command"
    headers = {"X-Api-Key": api_key()}
    with urllib.request.urlopen(urllib.request.Request(base, headers=headers), timeout=60) as response:
        commands = json.load(response)

    queued = sorted((c for c in commands if c.get("status") == "queued" and c.get("name") in SEARCHES),
                    key=lambda c: c.get("queued") or "")
    cancel = queued[:max(len(queued) - args.keep, 0)]
    others = sum(1 for c in commands if c.get("status") == "queued" and c.get("name") not in SEARCHES)
    print(f"queued searches: {len(queued)}, other queued tasks: {others}, cancelling: {len(cancel)}", flush=True)
    if args.dry_run:
        return 0

    failed, cancelled = 0, []
    for c in cancel:
        try:
            urllib.request.urlopen(urllib.request.Request(f"{base}/{c['id']}", method="DELETE", headers=headers), timeout=30)
            cancelled.append(c["id"])
        except urllib.error.HTTPError:
            failed += 1  # started in the meantime; Lidarr cannot cancel a running task
    print(f"cancelled {len(cancelled)}" + (f", {failed} had already started" if failed else ""), flush=True)

    if cancelled and args.db and os.path.exists(args.db):
        removed = 0
        db = sqlite3.connect(args.db, timeout=60)
        try:
            for i in range(0, len(cancelled), 500):
                chunk = cancelled[i:i + 500]
                # Status 0 = Queued: never touch a row Lidarr has started since
                removed += db.execute(f'DELETE FROM "Commands" WHERE "Status" = 0 AND "Id" IN ({",".join("?" * len(chunk))})',
                                      chunk).rowcount
            db.commit()
        finally:
            db.close()
        print(f"removed {removed} from {args.db}, so they stay gone after a restart", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
