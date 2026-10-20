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
   intro that shares the album's name). Every remaining hit becomes a release such as
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

## Setup

1. **SomeDL output template.** In the SomeDL WebUI (`http://<host>:5000`, settings) or in
   `somedl/SomeDL/config.toml`, set `download.output` to put every album in its own folder:
   `{album_artist}/{album}/{track_pos} - {song}`. Without this Lidarr cannot import, and the client's
   *Test* tells you so.
2. **Plugin.** In Lidarr: *System → Plugins*, paste `https://github.com/ddylvianen/lidarr-someDL-plugin`, install,
   restart. Without a GitHub release yet, unzip `Lidarr.Plugin.SomeDL.net8.0.zip` into
   `lidarr/plugins/ddylvianen/Lidarr.Plugin.SomeDL/` and restart Lidarr.
3. **Download client.** *Settings → Download Clients → + → SomeDL*: host `somedl`, port `5000`.
4. **Indexer.** *Settings → Indexers → + → SomeDL*: URL `http://somedl:5000`.
5. **Delay profile.** *Settings → Profiles → Delay Profiles*: tick **SomeDL**. New protocols start disabled,
   and until this is ticked every release is rejected with "SomeDLDownloadProtocol is not enabled".
6. **Quality profile.** Releases come in as the quality SomeDL actually produces (*Release Quality = Auto*):
   mp3 shows up as **MP3-VBR-V0**, opus/best as OGG Vorbis Q5, m4a as AAC-192. Allow that quality in the
   profile you use for these artists.

Indexer options: *Max Albums Per Search* (default 5; each one costs a lookup), *Only Matching Artist*, and a
manual *Release Quality* override.

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
   SOMEDL_URL=http://<host>:5000 python3 tools/somedl_mb_seed.py "artist album" --open
   ```
   Lidarr's metadata server picks up new MusicBrainz data within about a day. Then *Refresh & Scan* the
   artist in Lidarr.

   Add `--download` to have SomeDL download the album right away as well, so you don't have to wait for
   Lidarr to listen to it. When Lidarr knows the album later and searches it, SomeDL reports the files as
   already downloaded, so nothing is downloaded twice and Lidarr imports them within seconds (tested).
3. **Last.fm.** Last.fm has no way to add artists or albums. They appear there once scrobbled, and they only
   help Lidarr through option 1 (Tubifarry's Last.fm source). Lidarr's built-in Last.fm support is an import
   list (which artists to add), not a metadata source.

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
