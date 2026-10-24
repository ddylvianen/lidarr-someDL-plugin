# Lidarr.Plugin.SomeDL

Lidarr plugin that uses [SomeDL](https://github.com/ChemistryGull/SomeDL) as both an **indexer** (YouTube
Music album search) and a **download client** (yt-dlp download plus SomeDL's tagging: cover, lyrics, MusicBrainz
IDs). SomeDL runs as its own container; the plugin talks to its WebUI API.

Tested end to end on Lidarr nightly 3.1.6.5102 with SomeDL 1.7.1 (`ghcr.io/fant4zma/somedl`): search,
grab, download, import, blocklisting of failed grabs, removal. That covers MusicBrainz albums and, together
with Tubifarry's Deezer metadata, albums that are not on MusicBrainz.

## How it works

1. **Search.** Lidarr asks for an album. The plugin searches YouTube Music albums through SomeDL, drops hits by
   other artists, looks up each remaining hit's length, and drops lengths that cannot be the album (a single or
   intro that shares the album's name) or track counts that no wanted MusicBrainz release has (Lidarr refuses
   to import a download with a track more or less: "Has missing tracks"). Every remaining hit becomes a release such as
   `Artist - Album (2024) [MP3 V0 VBR] [WEB]`.
2. **Grab.** The client sends the album's YouTube Music playlist to SomeDL (`/yt-download`, the same call as
   SomeDL's own WebUI album button). SomeDL queues one item per track. The plugin keeps the mapping in
   `somedl-downloads.json` in Lidarr's config folder, so restarts of either side don't lose downloads.
3. **Track.** The Lidarr queue shows `x/y tracks done`. When every track has finished, the album folder
   goes to Lidarr, which imports it and moves the files out of SomeDL's folder. If every track failed, the
   download fails and the release is blocklisted, so Lidarr tries another one. If only some tracks failed,
   the queue item says how many, and Lidarr holds it with "Has missing tracks" until you import it by hand
   (*Manual Import*) or remove it with blocklisting.

## Install (Proxmox: Docker in an LXC or VM)

`deploy/docker-compose.yml` runs Lidarr nightly and SomeDL side by side, with one shared `/data` mount
(`/data/somedl` for downloads, `/data/music` for the library), so no Remote Path Mapping is needed. It also
updates yt-dlp on every SomeDL start: YouTube breaks older versions regularly (HTTP 403 on downloads).

On Proxmox, an unprivileged Debian LXC with `nesting=1,keyctl=1` and Docker works. Bind-mount your
storage into it, e.g. `pct set <ctid> -mp0 /tank/media,mp=/data`. In an unprivileged LXC, UID 1000 inside
is 101000 on the host, so give that UID write access to the storage.

```sh
mkdir -p /data/somedl /data/music && cd deploy && docker compose up -d
```

### Without Docker (Lidarr already runs as a systemd service)

`deploy/install-somedl.sh` installs SomeDL next to it: a Python venv in `/opt/somedl`, settings in
`/var/lib/somedl`, and a `somedl` systemd service (`deploy/somedl.service`). The service runs as the same user
as the `lidarr` service, updates yt-dlp on every start, and gets the per-album output template set for you,
so setup step 1 below is already done. Run it as root on the machine (LXC) where Lidarr runs:

```sh
curl -fsSLO https://raw.githubusercontent.com/ddylvianen/lidarr-someDL-plugin/main/deploy/install-somedl.sh
sh install-somedl.sh /data/somedl      # download folder; Lidarr must be able to reach it under this path
```

Running it again updates SomeDL. Logs: `journalctl -u somedl -f`. In Lidarr use host `127.0.0.1`, port `5000`.

**Match Lidarr's naming.** The mover and `--library` keep SomeDL's folder layout. Lidarr finds the files
on *Rescan* as long as the artist folder matches. To get Lidarr's default naming straight away, set SomeDL's
output template to `{album_artist}/{album} ({year})/{album_artist} - {album} - {track_pos} - {song}`. Turn on
*Rename Tracks* in Lidarr for the cases where the year or special characters still differ.

**Downloads from SomeDL's own WebUI into the library.** Give the library folder (Lidarr's root folder) as a
second argument: `sh install-somedl.sh /data/somedl /data/music`. A timer (`somedl-mover.timer`, every 5
minutes, `tools/somedl_mover.py`) then moves every album folder that has been untouched for 30 minutes into
the library as `Artist/Album/`. `SOMEDL_MOVER_LINK=1` hardlinks instead of moving. Albums Lidarr downloads
through the plugin are left alone: the plugin marks their folders with a `.lidarr-somedl` file (plugin 0.2.0.0
and newer) and Lidarr imports them itself. Log: `journalctl -u somedl-mover`.

**SomeDL in its own LXC.** Three things have to line up:

1. *One shared folder, mounted once, on the same path in both LXCs.* On the Proxmox host, mount the parent folder
   that holds both the downloads and the library, e.g.
   `pct set <lidarr-id> -mp0 /tank/media,mp=/data` and `pct set <somedl-id> -mp0 /tank/media,mp=/data`.
   Then `/data/somedl` and `/data/music` (your Lidarr root folder) are the same files in both. Mount the
   parent, not the two subfolders separately: moves and hardlinks don't work across two mount points.
2. *The same user ID.* Check which UID Lidarr runs as in its LXC (`ps -o uid= -C Lidarr`, usually 0 for
   root) and install with that UID: `SOMEDL_UID=0 sh install-somedl.sh /data/somedl`. In unprivileged LXCs
   the same UID inside maps to the same owner on the host, so Lidarr may move what SomeDL downloads.
3. *In Lidarr, the IP of the SomeDL LXC* as host (client) and `http://<ip>:5000` as URL (indexer).

## Setup

1. **SomeDL output template.** In the SomeDL WebUI (`http://<host>:5000`, settings) or in
   `somedl/SomeDL/config.toml`, set `download.output` to put every album in its own folder:
   `{album_artist}/{album}/{track_pos} - {song}`. Without this Lidarr cannot import, and the client's
   *Test* tells you so.
2. **Plugin.** In Lidarr: *System → Plugins*, paste `https://github.com/ddylvianen/lidarr-someDL-plugin`, install,
   restart. Without a GitHub release yet, unzip `Lidarr.Plugin.SomeDL.net8.0.zip` into
   `lidarr/plugins/ddylvianen/Lidarr.Plugin.SomeDL/` and restart Lidarr.
   Lidarr runs plugin installs and updates only when no other task runs, which can take very long with a
   large library. `deploy/update-plugin.sh` (run as root where Lidarr runs) installs the newest release
   directly and restarts Lidarr: of this plugin, of any plugin by `owner/repo` (`TypNull/Tubifarry`), or of
   all installed plugins with `--all`; `--check` only reports what would be updated.
3. **Download client.** *Settings → Download Clients → + → SomeDL*: host `somedl`, port `5000`.
4. **Indexer.** *Settings → Indexers → + → SomeDL*: URL `http://somedl:5000`.
5. **Delay profile.** *Settings → Profiles → Delay Profiles*: tick **SomeDL**. New protocols start disabled,
   and until this is ticked every release is rejected with "SomeDLDownloadProtocol is not enabled".
6. **Quality profile.** Releases come in as the quality SomeDL actually produces (*Release Quality = Auto*):
   mp3 shows up as **MP3-VBR-V0**, opus/best as OGG Vorbis Q5, m4a as AAC-192. Allow that quality in the
   profile you use for these artists.

Indexer options: *Max Albums Per Search* (default 5; each one costs a lookup), *Minimum Name Match* (default
80%: album title and artist must match at least this closely; case, accents, punctuation and a bracketed suffix
like "(Deluxe Edition)" are ignored), *Only Matching Artist*, and a manual *Release Quality* override.

When YouTube Music's album search has nothing with the wanted title (common for singles and EPs), the indexer
also searches songs and uses the album those songs belong to.

## Extra sources: Bandcamp, SoundCloud, Jamendo, Internet Archive, Audius

The plugin brings a second pair, **Extra sources (extradl)**, for music that is not (well) on YouTube. It
talks to `tools/extradl.py`, a small service next to SomeDL (port 5050) that searches these sites and
downloads without re-encoding. Tracks are tagged with the album and artist Lidarr searched for, the site's
track titles and the cover.

| Site | What | Lidarr quality |
|---|---|---|
| Bandcamp | its free stream | MP3-128 |
| SoundCloud | albums and singles; Go+ (30-second previews only) and DRM-protected releases are skipped | AAC-VBR (about 160 kbps) |
| Jamendo | free Creative Commons albums | FLAC |
| Internet Archive | live recordings (Live Music Archive), public domain recordings; files fetched directly | FLAC, FLAC 24bit or MP3-VBR |
| Audius | albums of verified artists only | MP3-320 |

Internet Archive concerts match a Lidarr album that has the same artist and the date (YYYY-MM-DD) in its
title, which is how MusicBrainz names most live recordings; of the tapes of one show, lossless and most
downloaded come first. Recordings the Archive only allows to be streamed (Grateful Dead soundboards, for one)
are skipped. Audius gives no year, because its dates are when an album was uploaded there.

- `install-somedl.sh` installs and starts it as `extradl.service`; the compose file has an `extradl` service.
- In Lidarr add one indexer per site you want: *Bandcamp*, *SoundCloud*, *Jamendo*, *Internet Archive* and
  *Audius (extradl)*, each with URL `http://<somedl-host>:5050`, so every site has its own priority, switch and
  failure status. One *Download Client → Extra sources (extradl)* with host `<somedl-host>`, port `5050` serves
  them all; tick **ExtraDL** in the delay profile. *Extra sources, combined (extradl)* still searches several
  sites from one indexer, for setups made before the separate ones existed.
- The same rules as the SomeDL indexer: title and artist must match for 80% (the artist's MusicBrainz aliases
  count, so Osees finds its Thee Oh Sees albums), the length must fit the album and the track count must be
  that of a wanted release. User playlists and fan
  uploads are skipped. Pick the sites per indexer.
- Audiomack is not included: yt-dlp's Audiomack support is broken at the moment.

## Music that is not on MusicBrainz

Lidarr only knows artists and albums from its metadata source, MusicBrainz by default. There are three ways
to handle what only exists on YouTube, Deezer, Spotify and so on:

1. **Use a different metadata source in Lidarr (tested).** [Tubifarry](https://github.com/TypNull/Tubifarry)
   adds metadata sources for Deezer, Discogs and Last.fm, plus a "Mixed" source that combines them with
   MusicBrainz. It runs fine alongside this plugin. Tested: with the Deezer source on, an artist with no
   MusicBrainz entry was added and an album was found by the SomeDL indexer, downloaded and imported 8/8.
   *Settings → Metadata → Deezer*: enable, set a User-Agent like `MyLidarr/1.0`, *Permanent* cache plus a
   cache folder. Tubifarry's Last.fm source is marked alpha by its own author.
2. **Add it to MusicBrainz (permanent, helps everyone).** `tools/somedl_mb_seed.py` builds a pre-filled
   MusicBrainz "Add release" form from a YouTube Music album. It fills in title, type, date, artist (linked
   if MusicBrainz knows them), tracklist with lengths, and the YouTube Music link. It also warns if the
   release seems to exist already. You check it and submit:
   ```sh
   somedl-seed "artist album"                                              # on the SomeDL machine (install-somedl.sh adds it)
   SOMEDL_URL=http://<host>:5000 python3 tools/somedl_mb_seed.py "artist album" --open   # anywhere else
   ```
   Instead of a search you can give a YouTube or YouTube Music link: an album (`music.youtube.com/browse/...`
   or `.../playlist?list=OLAK5uy_...`) or a song or video (`watch?v=...`, `youtu.be/...`), whose album is used.
   A plain upload that is not on YouTube Music (no song matching title and artist for 80%) becomes a single of
   its own: artist and title come from the video (you can correct them), year from the upload date, the
   thumbnail as a square cover. With `--download`/`--library` it is downloaded with yt-dlp and tagged by the
   script, because SomeDL would look it up on YouTube Music and could tag it as another song.
   On a machine without a browser (an LXC), add `--serve`: the script then prints a link like
   `http://<ip>:8099/` that you open on your PC or phone. Submitting stays a manual step on purpose:
   MusicBrainz has no API for adding releases, and automating its editor counts as a bot, which needs approval.
   Lidarr's metadata server picks up new MusicBrainz data within about a day. Then *Refresh & Scan* the
   artist in Lidarr.

   Add `--download` to have SomeDL download the album right away as well, so you don't have to wait for
   Lidarr to listen to it. When Lidarr knows the album later and searches it, SomeDL reports the files as
   already downloaded, so nothing is downloaded twice and Lidarr imports them within seconds (tested).

   Or use `--library /data/music` (run it on the machine where SomeDL saves its files). The script then waits
   until SomeDL is done and moves the album into your library, keeping SomeDL's folder layout.
   Your music player sees it right away, and once Lidarr knows the album, *Rescan* on the artist picks
   up the files where they are. Add `--link` to hardlink instead of move: the same file in both folders without
   using extra space. A symlink is not offered on purpose. It breaks as soon as Lidarr moves or renames the
   file it points to, and a player in another container cannot follow it.
3. **Last.fm.** Last.fm has no way to add artists or albums. They appear there once scrobbled, and they only
   help Lidarr through option 1 (Tubifarry's Last.fm source). Lidarr's built-in Last.fm support is an import
   list (which artists to add), not a metadata source.

## Keeping the library in FLAC (optional)

`deploy/install-music-flac.sh /data/music` installs a nightly job (`music-flac.timer`, 03:30,
`tools/music_flac.py`) for the machine that holds the music. It converts lossless files in other formats
(WAV, AIFF, ALAC, APE, WavPack) to FLAC and recompresses FLAC at the highest level. Both are bit-for-bit
lossless: a converted original is only deleted after the FLAC passed `flac -t`, and FLAC files `flac` refuses
(an ID3 tag in front) are re-encoded through ffmpeg and only replaced when the decoded audio is identical.
Lossy files (MP3, AAC, Opus) are not converted, since FLAC cannot restore what they lost. Every audio file
(FLAC, MP3, M4A, Opus, Ogg) does get its embedded covers optimized losslessly (optipng, jpegtran) and the empty
padding in its tags removed; the result only replaces the original when its audio packets are byte-for-byte the
same, its tags are the same and its covers decode to the same pixels. `--measure` reports what that would save
without changing anything. Files changed in the last
hour are skipped and finished files are remembered, so after the first run it only touches what is new. Put
`LIDARR_URL` and `LIDARR_API_KEY` in `/etc/default/music-flac` to let Lidarr rescan folders where a file got the
`.flac` name. Preview with `python3 /opt/music-flac/music_flac.py /data/music --dry-run --min-age 0`; see the space saved,
in total and per run, with `python3 /opt/music-flac/music_flac.py /data/music --stats`.

## Music you do not like (optional)

Add songs, albums or artists to a Navidrome playlist called **Niet leuk** from any Subsonic app (most can
add to a playlist even when they cannot rate). Every night `tools/navidrome_dislikes.py` makes Lidarr
stop monitoring each album whose songs are all in it, and each artist whose albums all are, so Lidarr
never searches for or upgrades them again. With `delete` it removes those albums and their files instead,
and adds them to the import list exclusions. Single songs only show up in its log: Lidarr manages whole
albums. Install in the Lidarr LXC (asks for the Navidrome password, then shows a dry run):

```sh
sh deploy/install-dislikes.sh http://<navidrome-ip>:4533 <navidrome-user> [unmonitor|delete]
```

## Soulseek downloads stuck in a queue (optional)

Tubifarry counts files waiting in another Soulseek user's queue ("Queued, Remotely") as active, so its
Timeout never fails them and Lidarr waits forever. `tools/slskd_unstick.py` cancels such files after a
few hours; Lidarr then fails the download, blocklists that release and grabs another source. Install in
the slskd LXC (reads the API key from slskd's config, starts with a dry run):

```sh
sh deploy/install-slskd-unstick.sh [hours, default 3] [slskd api key]
```

It also writes the users who banned you to `/data/slskd-ignore.txt`, which the Lidarr LXC sees at the
same path. Set that as **Ignore List** in the Slskd indexer (advanced) and Lidarr stops picking their
releases.

## Limits

- YouTube audio is lossy, roughly 128-160 kbps Opus/AAC. The release quality is what Lidarr will see after
  import, not a promise of anything better.
- One search costs SomeDL 1 + *Max Albums Per Search* requests. There is no RSS: YouTube Music has no feed of
  new releases, so Lidarr only finds things through (automatic) searches.
- SomeDL's WebUI endpoints are internal and may change between SomeDL versions. Everything that depends on
  them is in `SomeDL/SomeDLProxy.cs` and `SomeDLModels.cs`.
- SomeDL has no authentication. Keep port 5000 on a trusted network.

## Build

Needs Docker, not a local .NET SDK:

```sh
git clone --recurse-submodules https://github.com/ddylvianen/lidarr-someDL-plugin && cd lidarr-someDL-plugin
./build.sh            # -> Lidarr.Plugin.SomeDL.net8.0.zip
```

`src/Lidarr` is Lidarr's source at the commit the plugin builds against. `build.sh` stamps it with the oldest
Lidarr version the plugin supports (`MINIMUM_LIDARR_VERSION`), because Lidarr refuses plugins built against a
newer Lidarr.Core than itself. The GitHub workflow does the same and publishes a release on every version
bump, which is what Lidarr's *System → Plugins* installs and updates from.
