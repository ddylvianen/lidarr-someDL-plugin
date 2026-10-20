#!/usr/bin/env python3
"""
Keeps a music library in FLAC without losing any quality:

  1. lossless files in other formats (WAV, AIFF, ALAC .m4a, APE, WavPack) become FLAC; lossy files
     (MP3, AAC, Opus, Ogg) are left alone, since FLAC cannot bring back what they lost;
  2. FLAC files are recompressed at the highest level (flac -8), bit for bit the same audio;
  3. every audio file (FLAC, MP3, M4A, Opus, Ogg) gets its embedded covers optimized losslessly (optipng,
     jpegtran: the same pixels in fewer bytes) and the empty padding in its tags removed. The audio is not
     touched at all: the result only replaces the original when its audio packets are byte-for-byte the same,
     its tags are the same and its covers decode to the same pixels.

Tags and embedded covers are kept. A converted original is only deleted after the new FLAC passed
`flac -t`. Files changed in the last --min-age minutes are skipped (downloads, imports in progress), and
files already done are remembered in --state, so a nightly run only handles what is new.

  music_flac.py /data/music                      do it
  music_flac.py /data/music --dry-run            only show what would happen
  music_flac.py /data/music --lidarr http://lidarr:8686 --lidarr-key KEY
                                                 let Lidarr rescan folders where a file got a new name

  music_flac.py /data/music --stats              space saved so far, in total and per run
  music_flac.py /data/music --measure            what step 3 would save, without changing anything

Needs ffmpeg, ffprobe and flac; step 3 also python3-mutagen, optipng and jpegtran (skipped without them). install-music-flac.sh runs it from a systemd timer.
"""
import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import sys
import time
import urllib.request
from datetime import datetime

LOSSLESS_EXTENSIONS = {".wav", ".aif", ".aiff", ".ape", ".wv", ".m4a"}
TAGGED_EXTENSIONS = {".flac", ".mp3", ".m4a", ".opus", ".ogg", ".oga"}

try:
    import mutagen
    from mutagen.flac import Picture
    from mutagen.mp4 import MP4Cover
except ImportError:
    mutagen = None
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
        print(f"\n{'run':<17} {'files':>6} {'converted':>10} {'recompressed':>13} {'slimmed':>8} {'saved':>11}")
        for run_ in stats["runs"][-15:]:
            print(f"{run_['started'][:16].replace('T', ' '):<17} {run_['files']:>6} {run_['converted']:>10} "
                  f"{run_['recompressed']:>13} {run_.get('slimmed', 0):>8} {human(run_['saved_bytes']):>11}")


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


def packets_md5(path):
    """Hash of the compressed audio packets as they are stored: equal before and after means the audio
    itself was not touched at all (no decoding involved, so it is fast too)."""
    return run("ffmpeg", "-v", "error", "-i", path, "-map", "0:a", "-c", "copy", "-f", "md5", "-").stdout.strip()


def pixels_md5(path):
    return run("ffmpeg", "-v", "error", "-i", path, "-f", "md5", "-").stdout.strip()


IMAGE_CACHE = {}


def optimize_image(data):
    """The same image in fewer bytes, or the original. An album repeats its cover in every track, so each
    distinct image is optimized once."""
    key = hashlib.sha1(data).hexdigest()
    if key in IMAGE_CACHE:
        return IMAGE_CACHE[key]

    if data[:8] == b"\x89PNG\r\n\x1a\n":
        suffix, command = ".png", lambda src, dst: ["optipng", "-quiet", "-o2", "-out", dst, src]
    elif data[:2] == b"\xff\xd8":
        suffix, command = ".jpg", lambda src, dst: ["jpegtran", "-copy", "all", "-optimize", "-outfile", dst, src]
    else:
        IMAGE_CACHE[key] = data
        return data

    best = data
    with tempfile.TemporaryDirectory() as work:
        src, dst = os.path.join(work, "in" + suffix), os.path.join(work, "out" + suffix)
        with open(src, "wb") as f:
            f.write(data)
        if shutil.which(command(src, dst)[0]) and run(*command(src, dst)).returncode == 0 and os.path.exists(dst):
            with open(dst, "rb") as f:
                optimized = f.read()
            if len(optimized) < len(data) and pixels_md5(dst) == pixels_md5(src) != "":
                best = optimized
    IMAGE_CACHE[key] = best
    return best


def tag_snapshot(audio):
    """Every tag except the covers, to prove the rewrite changed nothing else."""
    skip = ("APIC", "covr", "metadata_block_picture")
    tags = audio.tags or {}
    items = tags.items() if hasattr(tags, "items") else []
    return sorted((str(k), repr(v)) for k, v in items if not str(k).startswith(skip))


def slim_tags(audio):
    """Replaces embedded covers with their optimized versions; returns the bytes saved on images."""
    saved = 0
    kind = type(audio).__name__

    if kind == "FLAC":
        for picture in audio.pictures:
            new = optimize_image(picture.data)
            saved += len(picture.data) - len(new)
            picture.data = new
    elif kind in ("MP3", "EasyMP3") and audio.tags is not None:
        for frame in audio.tags.getall("APIC"):
            new = optimize_image(frame.data)
            saved += len(frame.data) - len(new)
            frame.data = new
    elif kind in ("MP4", "EasyMP4") and audio.tags is not None and "covr" in audio.tags:
        covers = []
        for cover in audio.tags["covr"]:
            new = optimize_image(bytes(cover))
            saved += len(cover) - len(new)
            covers.append(MP4Cover(new, imageformat=cover.imageformat))
        audio.tags["covr"] = covers
    elif kind in ("OggVorbis", "OggOpus", "OggFLAC") and audio.tags is not None:
        encoded = []
        for value in audio.tags.get("metadata_block_picture", []):
            picture = Picture(base64.b64decode(value))
            new = optimize_image(picture.data)
            saved += len(picture.data) - len(new)
            picture.data = new
            encoded.append(base64.b64encode(picture.write()).decode("ascii"))
        if encoded:
            audio.tags["metadata_block_picture"] = encoded
    return saved


def save_without_padding(audio):
    if type(audio).__name__ in ("MP3", "EasyMP3"):
        # keep the ID3 version the file had: rewriting 2.3 as 2.4 would change what old players read
        version = audio.tags.version[1] if audio.tags is not None and audio.tags.version[1] in (3, 4) else 4
        audio.save(padding=lambda info: 0, v2_version=version)
    else:
        audio.save(padding=lambda info: 0)


def slim(path, commit=True):
    """Step 3 on one file. Returns (bytes saved, of which on images), (0, 0) when nothing was gained, or
    None on an error. Works on a copy that only replaces the original after every check passed."""
    folder, name = os.path.split(path)
    handle, temp = tempfile.mkstemp(prefix=".slim-", suffix=os.path.splitext(name)[1], dir=folder)
    os.close(handle)
    try:
        shutil.copy2(path, temp)
        audio = mutagen.File(temp)
        if audio is None:
            return (0, 0)
        before_tags = tag_snapshot(audio)
        image_saved = slim_tags(audio)
        save_without_padding(audio)

        saved = os.path.getsize(path) - os.path.getsize(temp)
        if saved <= 0:
            return (0, 0)
        if packets_md5(temp) != packets_md5(path) or tag_snapshot(mutagen.File(temp)) != before_tags:
            print(f"ERROR slimming {path}: the rewrite changed more than covers and padding; original kept")
            return None
        if commit:
            st = os.stat(path)
            os.replace(temp, path)
            os.utime(path, (st.st_atime, st.st_mtime))
        return (saved, min(max(image_saved, 0), saved))
    except Exception as e:  # a tag mutagen cannot parse must not stop the whole run
        print(f"ERROR slimming {path}: {e}")
        return None
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def measure(library, min_age):
    cutoff = time.time() - min_age * 60
    files = slimmable = total = images = 0
    for folder, _dirs, names in os.walk(library):
        for name in sorted(names):
            path = os.path.join(folder, name)
            if os.path.splitext(name)[1].lower() not in TAGGED_EXTENSIONS or os.path.getmtime(path) > cutoff:
                continue
            files += 1
            result = slim(path, commit=False)
            if result and result[0] > 0:
                slimmable += 1
                total += result[0]
                images += result[1]
            if files % 200 == 0:
                print(f"  {files} files looked at, {human(total)} to gain so far", flush=True)
    print(f"{files} audio files: {slimmable} could shrink by {human(total)} in total "
          f"({human(images)} from covers, {human(total - images)} from tag padding)")


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
    ap.add_argument("--measure", action="store_true", help="report what cover optimizing and padding removal "
                                                             "would save, without changing anything")
    ap.add_argument("--no-slim", action="store_true", help="skip step 3 (covers and tag padding)")
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

    slimming = not a.no_slim
    if slimming and mutagen is None:
        print("python3-mutagen is missing, so covers and tag padding are left alone (apt install python3-mutagen)")
        slimming = False
    if slimming:
        for tool in ("optipng", "jpegtran"):
            if not shutil.which(tool):
                print(f"{tool} is missing, so those covers are left as they are (apt install optipng libjpeg-turbo-progs)")

    if a.measure:
        if mutagen is None:
            sys.exit("--measure needs python3-mutagen")
        measure(os.path.abspath(a.library), a.min_age)
        return

    library = os.path.abspath(a.library)
    state = load_state(a.state)
    cutoff = time.time() - a.min_age * 60
    renamed_folders, converted, compressed, slimmed, handled = set(), 0, 0, 0, 0

    # Totals before this run, plus this run's own entry; both rewritten at every checkpoint, so the
    # numbers survive a run that is stopped halfway
    stats = load_stats(stats_path)
    base_saved, base_files = stats["saved_bytes"], stats["files"]
    this_run = {"started": datetime.now().isoformat(timespec="seconds"), "files": 0, "converted": 0,
                "recompressed": 0, "slimmed": 0, "saved_bytes": 0}
    stats["runs"] = stats["runs"][-99:] + [this_run]

    def checkpoint():
        if a.dry_run:
            return
        this_run.update(converted=converted, recompressed=compressed, slimmed=slimmed)
        stats.update(saved_bytes=base_saved + this_run["saved_bytes"], files=base_files + this_run["files"])
        save_state(a.state, state)
        save_state(stats_path, stats)

    for folder, _dirs, files in os.walk(library):
        for name in sorted(files):
            path = os.path.join(folder, name)
            extension = os.path.splitext(name)[1].lower()
            try:
                if os.path.getmtime(path) > cutoff:
                    continue
                current = fingerprint(path)
                size_before = os.path.getsize(path)
            except OSError:
                continue
            # [size, mtime] = converted/recompressed (also by older versions); + "slim" = step 3 done too
            done = state.get(path)
            if done == current + ["slim"] or (done == current and not slimming):
                continue
            only_slim = done == current

            if extension not in LOSSLESS_EXTENSIONS and extension not in TAGGED_EXTENSIONS:
                continue

            if not only_slim and extension in LOSSLESS_EXTENSIONS and codec_of(path).startswith(LOSSLESS_CODECS):
                new = to_flac(path, a.dry_run)
                if not new:
                    continue
                converted += 1
                renamed_folders.add(folder)
                state.pop(path, None)
                path = new

            if not only_slim and path.lower().endswith(".flac") and not a.no_recompress:
                if not recompress(path, a.dry_run):
                    continue  # not remembered, so the next run tries again
                compressed += 1

            # Last, because flac -8 writes fresh padding of its own
            if slimming and os.path.splitext(path)[1].lower() in TAGGED_EXTENSIONS:
                if a.dry_run:
                    print(f"would slim     {path}")
                else:
                    result = slim(path)
                    if result is None:
                        continue  # not remembered, so the next run tries again
                    if result[0] > 0:
                        slimmed += 1
                        print(f"slimmed        {path}  (-{human(result[0])})")

            if not a.dry_run and os.path.exists(path):
                state[path] = fingerprint(path) + (["slim"] if slimming else [])
                saved = size_before - os.path.getsize(path)
                if saved:
                    this_run["saved_bytes"] += saved
                    this_run["files"] += 1

            handled += 1
            if handled % 50 == 0:
                checkpoint()  # progress survives an interrupted first run

    # forget files that are gone, so the state does not grow forever
    state = {p: v for p, v in state.items() if os.path.exists(p)}
    if this_run["files"] == 0:
        stats["runs"].remove(this_run)  # quiet nights would only fill the table with zeros
    checkpoint()
    print(f"Done: {converted} converted to FLAC, {compressed} FLAC file(s) recompressed, {slimmed} slimmed, "
          f"saved {human(this_run['saved_bytes'])} this run")
    if not a.dry_run:
        print(f"Saved since {stats['since'][:10]}: {human(stats['saved_bytes'])} over {stats['files']} file(s)")

    if renamed_folders and a.lidarr and a.lidarr_key and not a.dry_run:
        lidarr_rescan(a.lidarr, a.lidarr_key, renamed_folders)


if __name__ == "__main__":
    main()
