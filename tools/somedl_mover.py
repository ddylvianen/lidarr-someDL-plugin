#!/usr/bin/env python3
"""
Moves albums downloaded through SomeDL's own WebUI into your music library.

Lidarr's downloads (through the SomeDL plugin) land in the same folder, but Lidarr imports those
itself; the plugin marks their folders with a '.lidarr-somedl' file and this script leaves them alone.
Everything else is moved once nothing in its folder has changed for --min-age minutes, keeping
SomeDL's Artist/Album layout, which is also the layout Lidarr uses: once Lidarr knows the album,
"Rescan" on the artist picks the files up where they are.

  somedl_mover.py /data/somedl /data/music            move
  somedl_mover.py /data/somedl /data/music --link     hardlink instead: the files stay in SomeDL's
                                                      folder as well, without taking extra space
  somedl_mover.py ... --dry-run                       only show what would happen

Meant to run every few minutes from a systemd timer (install-somedl.sh sets that up). Standard library only.
"""
import argparse
import os
import shutil
import sys
import time

LIDARR_MARKER = ".lidarr-somedl"     # written by the plugin's download client
LINKED_MARKER = ".somedl-linked"     # written here in --link mode, so a folder is linked only once
AUDIO = {".mp3", ".m4a", ".opus", ".ogg", ".oga", ".flac", ".webm", ".wav", ".aac"}
UNFINISHED = {".part", ".ytdl", ".temp", ".tmp"}


def album_folders(root):
    """Folders below root that directly hold audio files."""
    for folder, _dirs, files in os.walk(root):
        if folder != root and any(os.path.splitext(f)[1].lower() in AUDIO for f in files):
            yield folder, files


def idle_minutes(folder, files):
    newest = max(os.path.getmtime(os.path.join(folder, f)) for f in files + ["."])
    return (time.time() - newest) / 60


def transfer(src_root, folder, files, library, link, dry_run):
    target_dir = os.path.join(library, os.path.relpath(folder, src_root))
    moved = 0
    for name in files:
        if name in (LIDARR_MARKER, LINKED_MARKER):
            continue
        source, target = os.path.join(folder, name), os.path.join(target_dir, name)
        if os.path.exists(target):
            print(f"  skip, already in library: {target}")
            continue
        if not dry_run:
            os.makedirs(target_dir, exist_ok=True)
            os.link(source, target) if link else shutil.move(source, target)
        moved += 1
    return target_dir, moved


def remove_empty_parents(folder, root):
    root = root.rstrip("/")
    while folder.startswith(root + "/") and os.path.isdir(folder) and not os.listdir(folder):
        os.rmdir(folder)
        folder = os.path.dirname(folder)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("somedl_dir", help="SomeDL's download folder (its output_dir)")
    ap.add_argument("library", help="your music library (Lidarr's root folder)")
    ap.add_argument("--link", action="store_true", help="hardlink instead of move")
    ap.add_argument("--min-age", type=float, default=30, help="minutes a folder must be untouched first (default 30)")
    ap.add_argument("--dry-run", action="store_true", help="only print what would happen")
    a = ap.parse_args()

    src, library = os.path.abspath(a.somedl_dir), os.path.abspath(a.library)
    for path in (src, library):
        if not os.path.isdir(path):
            sys.exit(f"{path} does not exist")
    if library.startswith(src + "/") or src.startswith(library + "/"):
        sys.exit("The SomeDL folder and the library must not be inside each other")

    for folder, files in sorted(album_folders(src)):
        if LIDARR_MARKER in files or LINKED_MARKER in files:
            continue
        if any(os.path.splitext(f)[1].lower() in UNFINISHED for f in files):
            continue
        if idle_minutes(folder, files) < a.min_age:
            continue

        target, count = transfer(src, folder, files, library, a.link, a.dry_run)
        verb = ("would " if a.dry_run else "") + ("link" if a.link else "move")
        print(f"{verb} {count} files: {os.path.relpath(folder, src)} -> {target}")

        if a.dry_run:
            continue
        if a.link:
            with open(os.path.join(folder, LINKED_MARKER), "w") as f:
                f.write(f"Hardlinked into {target}\n")
        else:
            remove_empty_parents(folder, src)


if __name__ == "__main__":
    main()
