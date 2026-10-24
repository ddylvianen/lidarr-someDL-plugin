#!/usr/bin/env python3
"""Cancels slskd downloads that sit in another user's queue ("Queued, Remotely") for too long.

Tubifarry counts such files as active, so its own Timeout never fails them and Lidarr waits forever.
A cancelled file is a failed one to Tubifarry; with more than a fifth of an album failed, Lidarr marks
the download failed, blocklists that release and (with Redownload Failed on) grabs another source.

    slskd_unstick.py                    cancel files queued remotely for more than 3 hours
    slskd_unstick.py --hours 6          or another limit
    slskd_unstick.py --dry-run          only show them

It also keeps a list of the users who banned you (IGNORE_LIST, e.g. /data/slskd-ignore.txt, one name per
line). Point Tubifarry's Slskd indexer at it (Ignore List, advanced) and Lidarr stops picking releases of
users who refuse you anyway.

Settings come from the environment (the installer keeps them in /etc/slskd-unstick.env):
    SLSKD_URL (default http://127.0.0.1:5030), SLSKD_API_KEY, UNSTICK_HOURS (default 3), IGNORE_LIST
Standard library only.
"""

import argparse
import datetime
import json
import os
import sys
import urllib.parse
import urllib.request


def call(base, key, path, method="GET"):
    request = urllib.request.Request(base.rstrip("/") + "/api/v0/" + path, method=method, headers={"X-API-Key": key})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def parse_time(value):
    if not value:
        return None
    value = value.rstrip("Z")
    if "." in value:  # slskd sends 7 decimals, Python reads at most 6
        head, fraction = value.split(".", 1)
        value = head + "." + fraction[:6]
    try:
        moment = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.replace(tzinfo=datetime.timezone.utc) if moment.tzinfo is None else moment


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hours", type=float, default=float(os.environ.get("UNSTICK_HOURS", "3")))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--env-file", help="KEY=value lines, read literally")
    args = parser.parse_args()
    if args.env_file:
        for line in open(args.env_file, encoding="utf-8"):
            key, sep, value = line.rstrip("\n").partition("=")
            if sep and key.strip() and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value)
        if "UNSTICK_HOURS" in os.environ and "--hours" not in sys.argv:
            args.hours = float(os.environ["UNSTICK_HOURS"])

    base = os.environ.get("SLSKD_URL", "http://127.0.0.1:5030")
    key = os.environ.get("SLSKD_API_KEY") or sys.exit("SLSKD_API_KEY is not set")
    now = datetime.datetime.now(datetime.timezone.utc)
    limit = datetime.timedelta(hours=args.hours)

    stuck, banned = [], set()
    for user in call(base, key, "transfers/downloads") or []:
        for directory in user.get("directories", []):
            for f in directory.get("files", []):
                if "banned" in str(f.get("exception") or "").lower():
                    banned.add(user.get("username") or f.get("username"))
                if f.get("state") != "Queued, Remotely":
                    continue
                times = [t for t in (parse_time(f.get("enqueuedAt")), parse_time(f.get("requestedAt"))) if t]
                if times and now - max(times) > limit:
                    stuck.append((user.get("username") or f.get("username"), f))

    for username, f in stuck:
        name = f.get("filename", "").replace("\\", "/").rsplit("/", 2)
        label = "/".join(name[-2:])
        print(f"{'[dry run] ' if args.dry_run else ''}cancel: {username}: {label}", flush=True)
        if not args.dry_run:
            try:
                call(base, key, f"transfers/downloads/{urllib.parse.quote(username, safe='')}/{f['id']}?remove=false", "DELETE")
            except Exception as e:  # one refusal must not stop the rest
                print(f"  could not cancel: {e}", flush=True)
    if not stuck:
        print(f"nothing queued remotely for more than {args.hours:g} hours", flush=True)

    ignore_list = os.environ.get("IGNORE_LIST")
    if ignore_list and banned:
        try:
            known = {line.strip() for line in open(ignore_list, encoding="utf-8") if line.strip()}
        except OSError:
            known = set()
        new = sorted(b for b in banned if b and b not in known)
        for name in new:
            print(f"{'[dry run] ' if args.dry_run else ''}ignore from now on (banned you): {name}", flush=True)
        if new and not args.dry_run:
            with open(ignore_list, "a", encoding="utf-8") as out:
                out.writelines(name + "\n" for name in new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
