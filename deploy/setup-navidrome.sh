#!/bin/sh
# Points an installed Navidrome (Debian package or tarball with a systemd unit) at the Lidarr library
# and sets the options this setup relies on. Run as root in the Navidrome LXC:
#
#   sh setup-navidrome.sh [library folder]      default: /data/navidrome when it holds music
#
# Keeps a backup of the old config (navidrome.toml.bak) and any Last.fm keys already in it.
#
# Also installs (or updates, on a rerun) these plugins from their GitHub releases:
#   enabled:  apple-music (artist images/bios), coverartarchive (MusicBrainz covers), nd-lyrics (lyrics),
#             navibeat-mixes (time-of-day, rediscover and other mixes from your own plays),
#             mood-playlists (13 mood playlists and Instant Mix from audio analysis)
#   disabled until configured in Settings > Plugins: listenbrainz-daily-playlist
# NAVIDROME_PLUGINS=0 skips the plugins.
#
# mood-playlists needs an analyzer next to Navidrome (essentia-tensorflow, about 750 MB on disk). It is
# installed as the mood-analyzer service on 127.0.0.1:8765, reading the same files Navidrome reads, at
# low CPU and IO priority. MOOD_ANALYZER=0 skips it (and the plugin).
set -eu

ANALYZER_PORT=8765
ANALYZER_TAG=v0.2.0   # the analyzer that goes with the mood-playlists plugin release installed below
ANALYZER_DIR=/opt/mood-analyzer

CONFIG=$(systemctl cat navidrome 2>/dev/null | sed -n 's/.*"-c" "\([^"]*\)".*/\1/p' | head -1)
CONFIG=${CONFIG:-/etc/navidrome/navidrome.toml}
RUN_USER=$(systemctl show -p User --value navidrome 2>/dev/null || true)
RUN_USER=${RUN_USER:-navidrome}
DATA=$(sed -n 's/^DataFolder *= *"\(.*\)"/\1/p' "$CONFIG" 2>/dev/null | head -1)
DATA=${DATA:-/var/lib/navidrome}
OLD_MUSIC=$(sed -n 's/^MusicFolder *= *"\(.*\)"/\1/p' "$CONFIG" 2>/dev/null | head -1)
VERSION=$(navidrome --version 2>/dev/null | cut -d' ' -f1 || true)

echo "== Navidrome ${VERSION:-(version unknown)}, config $CONFIG, runs as $RUN_USER"

# navibeat-mixes has a build for 0.64+ that reads Navidrome's own play history; it must be saved
# under the plain name either way, which is the plugin's identity
NAVIBEAT=navibeat-mixes.ndp
case "$VERSION" in
    0.6[4-9].*|0.[7-9]*|[1-9]*) NAVIBEAT=navibeat-mixes-navidrome-0.64.ndp ;;
esac
MOOD=yes
[ "${MOOD_ANALYZER:-1}" = 0 ] && MOOD=skip

# repo  release-asset  saved-as  enable-right-away
PLUGINS="
navidrome/apple-music-plugin apple-music.ndp apple-music.ndp yes
sunsetroute1/navidrome-coverartarchive-plugin coverartarchive.ndp coverartarchive.ndp yes
J0R6IT0/navidrome-lyrics-plugin nd-lyrics.ndp nd-lyrics.ndp yes
nenadjokic/navibeat-mixes $NAVIBEAT navibeat-mixes.ndp yes
craiglush/navidrome-mood-plugin mood-playlists.ndp mood-playlists.ndp $MOOD
kgarner7/navidrome-listenbrainz-daily-playlist listenbrainz-daily-playlist.ndp listenbrainz-daily-playlist.ndp no
"
# Installed by earlier versions of this script, no longer wanted
REMOVED="navidrome-ntfy.ndp discord-rich-presence.ndp"

MUSIC=${1:-}
if [ -z "$MUSIC" ]; then
    if [ -d /data/navidrome ] && [ -n "$(ls -A /data/navidrome 2>/dev/null)" ]; then
        MUSIC=/data/navidrome
    else
        MUSIC=${OLD_MUSIC:-/data/navidrome}
    fi
fi
for d in "$OLD_MUSIC" "$MUSIC"; do
    [ -n "$d" ] || continue
    if [ -d "$d" ]; then
        echo "   $d: $(ls -A "$d" | wc -l) items on $(findmnt -no SOURCE --target "$d")"
    else
        echo "   $d: does not exist"
    fi
done
[ -d "$MUSIC" ] || { echo "Library folder $MUSIC does not exist"; exit 1; }
if ! su -s /bin/sh "$RUN_USER" -c "ls '$MUSIC' >/dev/null" 2>/dev/null; then
    echo "$RUN_USER cannot read $MUSIC; fix with: chmod -R o+rX $MUSIC"
    exit 1
fi
echo "== library: $MUSIC"

# Last.fm keys survive a rerun
key()    { sed -n "s/^$1 *= *\"\(.*\)\"/\1/p" "$CONFIG" 2>/dev/null | head -1; }
LFM_KEY=$(key 'LastFM.ApiKey'); LFM_SECRET=$(key 'LastFM.Secret')

[ -f "$CONFIG" ] && cp "$CONFIG" "$CONFIG.bak"
mkdir -p "$(dirname "$CONFIG")" "$DATA/plugins"
cat > "$CONFIG" <<TOML
DataFolder  = "$DATA"
MusicFolder = "$MUSIC"

# New imports show up within seconds (watcher); a full scan as a safety net
Scanner.Schedule    = "@every 6h"
Scanner.WatcherWait = "10s"

# Artist images, biographies, similar artists; tried left to right. coverartarchive first: Lidarr tags
# every album with its MusicBrainz id, which is exactly what it looks covers up by.
Agents = "coverartarchive,apple-music,deezer,lastfm"
$( [ -n "$LFM_KEY" ] && echo "LastFM.ApiKey = \"$LFM_KEY\"" || echo '# LastFM.ApiKey = ""   (https://www.last.fm/api/account/create)' )
$( [ -n "$LFM_SECRET" ] && echo "LastFM.Secret = \"$LFM_SECRET\"" || echo '# LastFM.Secret = ""' )
ListenBrainz.Enabled = true

CoverArtPriority = "cover.*, folder.*, front.*, embedded, external"
LyricsPriority   = ".lrc,.txt,embedded,nd-lyrics"
EnableSharing    = true

Plugins.Enabled = true
Plugins.Folder  = "$DATA/plugins"
TOML
chown "$RUN_USER:" "$DATA/plugins"

if [ "${NAVIDROME_PLUGINS:-1}" != 0 ]; then
    echo "== plugins"
    echo "$PLUGINS" | while read -r repo asset target enable; do
        [ -n "$repo" ] && [ "$enable" != skip ] || continue
        if curl -fsSL "https://github.com/$repo/releases/latest/download/$asset" -o "$DATA/plugins/$target.part"; then
            mv "$DATA/plugins/$target.part" "$DATA/plugins/$target"
            echo "   ${target%.ndp}: installed ($asset, latest release of $repo)"
        else
            rm -f "$DATA/plugins/$target.part"
            echo "   ${target%.ndp}: download FAILED ($repo)"
        fi
    done
    for old in $REMOVED; do
        [ -f "$DATA/plugins/$old" ] && rm -f "$DATA/plugins/$old" && echo "   ${old%.ndp}: removed"
    done
    chown -R "$RUN_USER:" "$DATA/plugins"
fi

# --- mood analyzer ---------------------------------------------------------------------------------
# Runs here, not in another container, because the plugin hands it file paths as Navidrome sees them
if [ "${NAVIDROME_PLUGINS:-1}" != 0 ] && [ "$MOOD" = yes ]; then
    echo "== mood analyzer"
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv ffmpeg curl >/dev/null
    PY=$(python3 -c 'import sys; print("%d%02d" % sys.version_info[:2])')
    FREE_MB=$(df -Pm "$(dirname "$ANALYZER_DIR")" | awk 'NR==2 {print $4}')
    # essentia-tensorflow builds: dev1389 for Python 3.9 to 3.13, dev1438 only for 3.14
    ESSENTIA=2.1b6.dev1389
    [ "$PY" = 314 ] && ESSENTIA=2.1b6.dev1438
    if [ "$PY" -lt 309 ] || [ "$PY" -gt 314 ]; then
        echo "   skipped: essentia-tensorflow has no build for Python $(python3 -V | cut -d' ' -f2) (needs 3.9 to 3.14)"
        MOOD=failed
    elif [ ! -x "$ANALYZER_DIR/bin/uvicorn" ] && [ "$FREE_MB" -lt 1200 ]; then
        echo "   skipped: needs about 1.2 GB free on $(dirname "$ANALYZER_DIR"), there is ${FREE_MB} MB"
        echo "   grow the disk on the Proxmox host (pct resize <id> rootfs +4G) and run this again"
        MOOD=failed
    else
        # set -e does not reach into the steps below once one of them is tested, so each is checked
        ok=1
        [ -x "$ANALYZER_DIR/bin/python" ] || python3 -m venv "$ANALYZER_DIR" || ok=0
        if [ $ok = 1 ]; then
            echo "   installing essentia-tensorflow (about 300 MB download, takes a few minutes)"
            "$ANALYZER_DIR/bin/pip" install --quiet --disable-pip-version-check --no-cache-dir -U \
                "fastapi>=0.115" "uvicorn>=0.34" "essentia-tensorflow==$ESSENTIA" "mutagen>=1.47" "numpy>=2.0" || ok=0
        fi
        [ $ok = 1 ] && { curl -fsSL "https://raw.githubusercontent.com/craiglush/navidrome-mood-plugin/$ANALYZER_TAG/analyzer-service/app.py" \
            -o "$ANALYZER_DIR/app.py" || ok=0; }
        mkdir -p "$ANALYZER_DIR/models"
        for model in feature-extractors/discogs-effnet/discogs-effnet-bs64-1.pb \
            classification-heads/mood_happy/mood_happy-discogs-effnet-1.pb \
            classification-heads/mood_sad/mood_sad-discogs-effnet-1.pb \
            classification-heads/mood_relaxed/mood_relaxed-discogs-effnet-1.pb \
            classification-heads/mood_aggressive/mood_aggressive-discogs-effnet-1.pb \
            classification-heads/mood_party/mood_party-discogs-effnet-1.pb \
            classification-heads/danceability/danceability-discogs-effnet-1.pb; do
            [ $ok = 1 ] || break
            file="$ANALYZER_DIR/models/$(basename "$model")"
            [ -s "$file" ] || curl -fsSL "https://essentia.upf.edu/models/$model" -o "$file" || { rm -f "$file"; ok=0; }
        done
    fi
    if [ "$MOOD" = yes ] && [ "${ok:-0}" = 0 ]; then
        echo "   install FAILED (see above); the mood-playlists plugin stays off"
        MOOD=failed
    fi
    if [ "$MOOD" = yes ]; then
        chown -R "$RUN_USER:" "$ANALYZER_DIR"

        # Low priority on purpose: analysing a library takes hours, and must never slow down playback
        # or the other containers on the host
        cat > /etc/systemd/system/mood-analyzer.service <<UNIT
[Unit]
Description=Mood analyzer for Navidrome's mood-playlists plugin
After=network.target

[Service]
User=$RUN_USER
WorkingDirectory=$ANALYZER_DIR
Environment=MODELS_DIR=$ANALYZER_DIR/models
Environment=TF_CPP_MIN_LOG_LEVEL=2
ExecStart=$ANALYZER_DIR/bin/uvicorn app:app --host 127.0.0.1 --port $ANALYZER_PORT
Restart=on-failure
RestartSec=30
Nice=15
IOSchedulingClass=idle
CPUQuota=200%
MemoryHigh=1500M
MemoryMax=2500M

[Install]
WantedBy=multi-user.target
UNIT
        systemctl daemon-reload
        systemctl enable mood-analyzer >/dev/null 2>&1
        systemctl restart mood-analyzer
        i=0
        until curl -sf "http://127.0.0.1:$ANALYZER_PORT/health" >/dev/null; do
            i=$((i + 1))
            [ $i -lt 30 ] || break
            sleep 2
        done
        if curl -sf "http://127.0.0.1:$ANALYZER_PORT/health" | grep -q '"models_available": *true'; then
            echo "   runs on 127.0.0.1:$ANALYZER_PORT"
        else
            echo "   did NOT start; see: journalctl -u mood-analyzer -n 30"
            MOOD=failed
        fi
        MEM_MB=$(free -m | awk '/^Mem:/ {print $2}')
        [ "$MEM_MB" -ge 3000 ] || echo "   note: this LXC has ${MEM_MB} MB RAM; analysis wants about 1.5 GB of it while it runs"
    fi
fi

systemctl restart navidrome
sleep 5
echo "== log after restart"
journalctl -u navidrome -n 80 --no-pager -o cat | grep -iE "error|warn|unknown|invalid|music folder|library|plugin" | grep -viE "not found in deezer|usable artist page" | tail -20 || true

if [ "${NAVIDROME_PLUGINS:-1}" != 0 ] && systemctl is-active --quiet navidrome; then
    nd() { su -s /bin/sh "$RUN_USER" -c "cd '$DATA' && navidrome -c '$CONFIG' plugin $*"; }
    nd rescan >/dev/null 2>&1 || true

    if [ "$MOOD" = yes ]; then
        printf '{"analyzer_url":"http://127.0.0.1:%s"}\n' "$ANALYZER_PORT" > "$DATA/mood-playlists.json"
        chown "$RUN_USER:" "$DATA/mood-playlists.json"
        nd edit mood-playlists --config-file "$DATA/mood-playlists.json" >/dev/null 2>&1 \
            || echo "   mood-playlists: set Analyzer Service URL to http://127.0.0.1:$ANALYZER_PORT in Settings > Plugins"
    fi

    echo "== enabling"
    echo "$PLUGINS" | while read -r repo asset target enable; do
        [ "$enable" = yes ] && [ -f "$DATA/plugins/$target" ] || continue
        [ "$target" = mood-playlists.ndp ] && [ "$MOOD" != yes ] && continue
        id=${target%.ndp}
        # Plugins that act per user or per library get all of them; the others refuse the flag, harmlessly
        nd edit "$id" --all-users >/dev/null 2>&1 || true
        nd edit "$id" --all-libraries >/dev/null 2>&1 || true
        nd enable "$id" >/dev/null 2>&1 && echo "   $id: enabled" \
            || echo "   $id: could not enable; open it in Settings > Plugins to see why"
    done
    nd list 2>/dev/null || true
    echo "   listenbrainz-daily-playlist is installed but off: configure and enable it in Settings > Plugins"
fi

systemctl is-active --quiet navidrome && echo "== Navidrome runs; old config kept as $CONFIG.bak" \
    || { echo "== Navidrome did NOT start; restore with: cp $CONFIG.bak $CONFIG && systemctl restart navidrome"; exit 1; }
