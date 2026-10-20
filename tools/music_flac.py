#!/usr/bin/env python3
"""
Keeps a music library in FLAC without losing any quality:

  1. lossless files in other formats (WAV, AIFF, ALAC .m4a, APE, WavPack) become FLAC; lossy files
     (MP3, AAC, Opus, Ogg) are left alone, since FLAC cannot bring back what they lost;
  2. FLAC files are recompressed at the highest level (flac -8), bit for bit the same audio.

Tags and embedded covers are kept. A converted original is only deleted after the new FLAC passed
`flac -t`. Files changed in the last --min-age minutes are skipped (downloads, imports in progress), and
files already done are remembered in --state, so a nightly run only handles what is new.

  music_flac.py /data/music                      do it
  music_flac.py /data/music --dry-run            only show what would happen
  music_flac.py /data/music --lidarr http://lidarr:8686 --lidarr-key KEY
                                                 let Lidarr rescan folders where a file got a new name

  music_flac.py /data/music --stats              space saved so far, in total and per run

Needs ffmpeg, ffprobe and flac. Standard library only. install-music-flac.sh runs it from a systemd timer.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

LOSSLESS_EXTENSIONS = {".wav", ".aif", ".aiff", ".ape", ".wv", ".m4a"}
LOSSLESS_CODECS = ("pcm_", "alac", "ape", "wavpack")


def run(*args):
    return subprocess.run(args, capture_output=True, text=True)


def codec_of(path):
    result = run("ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
                 "-of", "csv=p=0", path)
    return result.stdout.strip()


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(temp, path)


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.2f} TB"


def load_stats(path):
    stats = load_state(path)
    stats.setdefault("saved_bytes", 0)
    stats.setdefault("files", 0)
    stats.setdefault("since", datetime.now().isoformat(timespec="seconds"))
    stats.setdefault("runs", [])
    return stats


def show_stats(path):
    stats = load_stats(path)
    print(f"Saved since {stats['since'][:10]}: {human(stats['saved_bytes'])} over {stats['files']} file(s)")
    if stats["runs"]:
        print(f"\n{'run':<20} {'files':>6} {'converted':>10} {'recompressed':>13} {'saved':>11}")
        for run_ in stats["runs"][-15:]:
            print(f"{run_['started'][:16].replace('T', ' '):<20} {run_['files']:>6} {run_['converted']:>10} "
                  f"{run_['recompressed']:>13} {human(run_['saved_bytes']):>11}")


def fingerprint(path):
    st = os.stat(path)
    return [st.st_size, int(st.st_mtime)]


def to_flac(source, dry_run):
    """Lossless file -> FLAC next to it. Returns the new path, or None."""
    target = os.path.splitext(source)[0] + ".flac"
    if os.path.exists(target):
        return None
    if dry_run:
        print(f"would convert  {source}")
        return target

    result = run("ffmpeg", "-nostdin", "-v", "error", "-i", source, "-map", "0:a", "-map", "0:v?",
                 "-map_metadata", "0", "-c:a", "flac", "-compression_level", "12", "-c:v", "copy",
                 "-disposition:v", "attached_pic", target)
    if result.returncode != 0 or run("flac", "-t", "-s", target).returncode != 0:
        if os.path.exists(target):
            os.remove(target)
        print(f"ERROR converting {source}: {result.stderr.strip()[:300]}")
        return None

    st = os.stat(source)
    os.utime(target, (st.st_atime, st.st_mtime))
    os.remove(source)
    print(f"converted      {target}")
    return target


def recompress(path, dry_run):
    """flac -8 in place; flac writes a temporary file and verifies it before replacing the original."""
    if dry_run:
        print(f"would compress {path}")
        return True
    before = os.path.getsize(path)
    result = run("flac", "-8", "-f", "-V", "-s", "--preserve-modtime", path)
    if result.returncode != 0 and not recompress_with_ffmpeg(path):
        print(f"ERROR compressing {path}: {result.stderr.strip()[:300]}")
        return False
    saved = before - os.path.getsize(path)
    if saved > 0:
        print(f"compressed     {path}  (-{saved / 1048576:.1f} MB)")
    return True


def audio_md5(path):
    return run("ffmpeg", "-v", "error", "-i", path, "-map", "0:a", "-f", "md5", "-").stdout.strip()


def recompress_with_ffmpeg(path):
    """For FLAC files flac refuses, mostly ones with an ID3v2 tag in front (which FLAC does not allow;
    players and Lidarr use the Vorbis comments anyway). Replaced only if the decoded audio is identical."""
    temp = path + ".recompress.flac"
    result = run("ffmpeg", "-nostdin", "-v", "error", "-i", path, "-map", "0:a", "-map", "0:v?", "-map_metadata", "0",
                 "-c:a", "flac", "-compression_level", "12", "-c:v", "copy", "-disposition:v", "attached_pic", "-y", temp)
    ok = result.returncode == 0 and run("flac", "-t", "-s", temp).returncode == 0 and audio_md5(temp) == audio_md5(path)
    if not ok:
        if os.path.exists(temp):
            os.remove(temp)
        return False
    st = os.stat(path)
    os.replace(temp, path)
    os.utime(path, (st.st_atime, st.st_mtime))
    return True


def lidarr_rescan(url, key, folders):
    body = json.dumps({"name": "RescanFolders", "folders": sorted(folders)}).encode()
    request = urllib.request.Request(url.rstrip("/") + "/api/v1/command", data=body, method="POST",
                                     headers={"X-Api-Key": key, "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=30).close()
        print(f"Asked Lidarr to rescan {len(folders)} folder(s)")
    except OSError as e:
        print(f"Could not reach Lidarr ({e}); its next scheduled rescan will pick the new files up")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("library")
    ap.add_argument("--state", default="/var/lib/music-flac/state.json", help="remembers finished files")
    ap.add_argument("--min-age", type=float, default=60, help="skip files changed in the last N minutes (default 60)")
    ap.add_argument("--no-recompress", action="store_true", help="only convert, leave existing FLAC alone")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stats", action="store_true", help="show the space saved so far and exit")
    ap.add_argument("--lidarr", default=os.environ.get("LIDARR_URL"), help="Lidarr URL (or LIDARR_URL)")
    ap.add_argument("--lidarr-key", default=os.environ.get("LIDARR_API_KEY"), help="Lidarr API key (or LIDARR_API_KEY)")
    a = ap.parse_args()
    stats_path = os.path.join(os.path.dirname(a.state) or ".", "stats.json")
    if a.stats:
        show_stats(stats_path)
        return

    for tool in ("ffmpeg", "ffprobe", "flac"):
        if run("which", tool).returncode != 0:
            sys.exit(f"{tool} is missing (apt install ffmpeg flac)")

    library = os.path.abspath(a.library)
    state = load_state(a.state)
    cutoff = time.time() - a.min_age * 60
    renamed_folders, converted, compressed, handled = set(), 0, 0, 0

    # Totals before this run, plus this run's own entry; both rewritten at every checkpoint, so the
    # numbers survive a run that is stopped halfway
    stats = load_stats(stats_path)
    base_saved, base_files = stats["saved_bytes"], stats["files"]
    this_run = {"started": datetime.now().isoformat(timespec="seconds"), "files": 0, "converted": 0,
                "recompressed": 0, "saved_bytes": 0}
    stats["runs"] = stats["runs"][-99:] + [this_run]

    def checkpoint():
        if a.dry_run:
            return
        this_run.update(converted=converted, recompressed=compressed)
        stats.update(saved_bytes=base_saved + this_run["saved_bytes"], files=base_files + this_run["files"])
        save_state(a.state, state)
        save_state(stats_path, stats)

    for folder, _dirs, files in os.walk(library):
        for name in sorted(files):
            path = os.path.join(folder, name)
            extension = os.path.splitext(name)[1].lower()
            try:
                if os.path.getmtime(path) > cutoff or state.get(path) == fingerprint(path):
                    continue
                size_before = os.path.getsize(path)
            except OSError:
                continue

            if extension in LOSSLESS_EXTENSIONS:
                if not codec_of(path).startswith(LOSSLESS_CODECS):
                    state[path] = fingerprint(path)  # lossy (AAC in .m4a): leave alone, do not look again
                    continue
                new = to_flac(path, a.dry_run)
                if not new:
                    continue
                converted += 1
                renamed_folders.add(folder)
                state.pop(path, None)
                path = new

            if path.lower().endswith(".flac"):
                if not a.no_recompress:
                    if not recompress(path, a.dry_run):
                        continue  # not remembered, so the next run tries again
                    compressed += 1
                if not a.dry_run and os.path.exists(path):
                    state[path] = fingerprint(path)
                    this_run["saved_bytes"] += size_before - os.path.getsize(path)
                    this_run["files"] += 1

            handled += 1
            if handled % 50 == 0:
                checkpoint()  # progress survives an interrupted first run

    # forget files that are gone, so the state does not grow forever
    state = {p: v for p, v in state.items() if os.path.exists(p)}
    if this_run["files"] == 0:
        stats["runs"].remove(this_run)  # quiet nights would only fill the table with zeros
    checkpoint()
    print(f"Done: {converted} converted to FLAC, {compressed} FLAC file(s) recompressed, "
          f"saved {human(this_run['saved_bytes'])} this run")
    if not a.dry_run:
        print(f"Saved since {stats['since'][:10]}: {human(stats['saved_bytes'])} over {stats['files']} file(s)")

    if renamed_folders and a.lidarr and a.lidarr_key and not a.dry_run:
        lidarr_rescan(a.lidarr, a.lidarr_key, renamed_folders)


if __name__ == "__main__":
    main()
