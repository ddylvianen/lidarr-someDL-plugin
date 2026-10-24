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
#             mood-playlists (13 mood playlists and Instant Mix from audio analysis),
#             genre-mixes (one auto-refreshed playlist per genre)
#   disabled until configured in Settings > Plugins: listenbrainz-daily-playlist,
#             listenbrainz (two-way sync of favourites with ListenBrainz loves; needs your token)
# NAVIDROME_PLUGINS=0 skips the plugins.
#
# mood-playlists needs an analyzer next to Navidrome (essentia-tensorflow, about 750 MB on disk). It is
# installed as the mood-analyzer service on 127.0.0.1:8765, reading the same files Navidrome reads, at
# low CPU and IO priority. MOOD_ANALYZER=0 skips it (and the plugin).
#
# Plugin updates, daily at 04:30 (navidrome-plugin-update.timer): ndpm (github.com/depau/ndpm) fetches new
# releases of the plugins above, the plugins Navidrome switched off for the update are switched back on,
# mood-playlists moves to the author's release once there is a fixed one, and the plugin watcher
# (github.com/Tal0na/plugin-watcher-0navidrome) reports newer releases of any plugin nothing updates.
# Give the watcher somewhere to send them, e.g. ntfy:
#   NDP_WEBHOOK_URL=https://ntfy.sh/<your-topic> NDP_WEBHOOK_TYPE=ntfy sh setup-navidrome.sh /data
# (discord, slack and generic work too). PLUGIN_UPDATES=0 skips all of this.
set -eu

NDPM_VERSION=v0.1.0
WATCHER_SRC=https://raw.githubusercontent.com/Tal0na/plugin-watcher-0navidrome/4d971a5b38ce50b453a38157a8c15c45cb9b5829/ndp_watcher.py

ANALYZER_PORT=8765
# mood-playlists v0.2.0 is refused by Navidrome 0.64. Until the author releases something newer, this
# repo carries a build of the unreleased fix (deploy/navidrome-plugins/README.md) and the analyzer comes
# from that same commit; once a newer release exists, plugin and analyzer come from it instead.
MOOD_REPO=craiglush/navidrome-mood-plugin
MOOD_BROKEN_RELEASE=v0.2.0
MOOD_TAG=$(curl -fsSL "https://api.github.com/repos/$MOOD_REPO/releases/latest" 2>/dev/null \
    | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)
if [ -n "$MOOD_TAG" ] && [ "$MOOD_TAG" != "$MOOD_BROKEN_RELEASE" ]; then
    MOOD_NDP_URL=$MOOD_REPO
    ANALYZER_SRC=https://raw.githubusercontent.com/$MOOD_REPO/$MOOD_TAG/analyzer-service/app.py
else
    MOOD_NDP_URL=https://raw.githubusercontent.com/ddylvianen/lidarr-someDL-plugin/main/deploy/navidrome-plugins/mood-playlists.ndp
    ANALYZER_SRC=https://raw.githubusercontent.com/WernerVdM97/navidrome-mood-plugin/7fd4862ea9f20a3b741f6c49cc4d74a71557d4a6/analyzer-service/app.py
fi
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

# repo (or a direct URL; +repo also takes pre-releases)  release-asset  saved-as  enable-right-away
PLUGINS="
navidrome/apple-music-plugin apple-music.ndp apple-music.ndp yes
sunsetroute1/navidrome-coverartarchive-plugin coverartarchive.ndp coverartarchive.ndp yes
J0R6IT0/navidrome-lyrics-plugin nd-lyrics.ndp nd-lyrics.ndp yes
nenadjokic/navibeat-mixes $NAVIBEAT navibeat-mixes.ndp yes
$MOOD_NDP_URL mood-playlists.ndp mood-playlists.ndp $MOOD
Aut0iq/navidrome-genre-mixes genre-mixes.ndp genre-mixes.ndp yes
kgarner7/navidrome-listenbrainz-daily-playlist listenbrainz-daily-playlist.ndp listenbrainz-daily-playlist.ndp no
+egecelikci/navidrome-listenbrainz-plugin navidrome-listenbrainz-plugin.ndp navidrome-listenbrainz-plugin.ndp no
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

# mood-playlists answers "similar songs" (Instant Mix, and players such as Music Assistant's "Don't stop
# the music") from the audio analysis; it has nothing for images or biographies, which fall through
AGENTS="coverartarchive,apple-music,deezer,lastfm"
[ "$MOOD" = yes ] && [ "${NAVIDROME_PLUGINS:-1}" != 0 ] && AGENTS="coverartarchive,apple-music,mood-playlists,deezer,lastfm"

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
Agents = "$AGENTS"
$( [ -n "$LFM_KEY" ] && echo "LastFM.ApiKey = \"$LFM_KEY\"" || echo '# LastFM.ApiKey = ""   (https://www.last.fm/api/account/create)' )
$( [ -n "$LFM_SECRET" ] && echo "LastFM.Secret = \"$LFM_SECRET\"" || echo '# LastFM.Secret = ""' )
ListenBrainz.Enabled = true

CoverArtPriority = "cover.*, folder.*, front.*, embedded, external"
LyricsPriority   = ".lrc,.txt,embedded,nd-lyrics"
EnableSharing    = true

Plugins.Enabled = true
Plugins.Folder  = "$DATA/plugins"
# Picks up plugin files that ndpm replaces, without a restart
Plugins.AutoReload = true
TOML
chown "$RUN_USER:" "$DATA/plugins"

if [ "${NAVIDROME_PLUGINS:-1}" != 0 ]; then
    echo "== plugins"
    echo "$PLUGINS" | while read -r repo asset target enable; do
        [ -n "$repo" ] && [ "$enable" != skip ] || continue
        case "$repo" in
            https://*) url=$repo; from="the build in this repo" ;;
            +*)
                repo=${repo#+}
                # newest release of any kind: GitHub's "latest" skips pre-releases
                url=$(curl -fsSL "https://api.github.com/repos/$repo/releases?per_page=1" 2>/dev/null \
                    | grep -o "\"browser_download_url\": *\"[^\"]*/$asset\"" | head -1 | sed 's/.*"\(http[^"]*\)"/\1/')
                from="$asset, newest release of $repo" ;;
            *) url="https://github.com/$repo/releases/latest/download/$asset"; from="$asset, latest release of $repo" ;;
        esac
        if curl -fsSL "$url" -o "$DATA/plugins/$target.part"; then
            mv "$DATA/plugins/$target.part" "$DATA/plugins/$target"
            echo "   ${target%.ndp}: installed ($from)"
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
        [ $ok = 1 ] && { curl -fsSL "$ANALYZER_SRC" \
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

# --- plugin updates: ndpm, re-enabling, mood-playlists release switch, watcher ---------------------------
if [ "${NAVIDROME_PLUGINS:-1}" != 0 ] && [ "${PLUGIN_UPDATES:-1}" != 0 ]; then
    echo "== plugin updates"
    case "$(uname -m)" in
        x86_64) ARCH=amd64 ;;
        aarch64|arm64) ARCH=arm64 ;;
        armv7l|armv6l) ARCH=arm ;;
        *) ARCH= ;;
    esac
    if [ -n "$ARCH" ] && curl -fsSL "https://github.com/depau/ndpm/releases/download/$NDPM_VERSION/ndpm-linux-$ARCH" -o /usr/local/bin/ndpm.part; then
        mv /usr/local/bin/ndpm.part /usr/local/bin/ndpm
        chmod 755 /usr/local/bin/ndpm
        echo "   ndpm $NDPM_VERSION installed"
    else
        rm -f /usr/local/bin/ndpm.part
        echo "   ndpm: no build for $(uname -m) or download FAILED; plugins are only updated by rerunning this script"
    fi

    # ndpm follows the GitHub releases of every plugin above; the mood-playlists build from this repo is
    # left to the update script below until the author releases a fixed version
    {
        echo "plugins_dir: $DATA/plugins"
        echo "sources:"
        echo "$PLUGINS" | while read -r repo asset target enable; do
            case "$repo" in ''|https://*) continue ;; esac
            [ "$enable" = skip ] && continue
            pre=false
            case "$repo" in +*) pre=true; repo=${repo#+} ;; esac
            echo "  - repo: $repo"
            echo "    filename: $asset"
            echo "    slug: ${target%.ndp}"
            echo "    include_prereleases: $pre"
        done
    } > /etc/ndpm.yaml

    apt-get install -y -qq python3 >/dev/null 2>&1 || true
    mkdir -p /opt/ndp-watcher
    curl -fsSL "$WATCHER_SRC" -o /opt/ndp-watcher/ndp_watcher.py || echo "   plugin watcher: download FAILED"
    # A rerun keeps the webhook already set, unless a new one is given
    python3 - "$DATA/plugins" "${NDP_WEBHOOK_URL:-}" "${NDP_WEBHOOK_TYPE:-}" <<'WATCHERCONFIG'
import json, sys
path = "/opt/ndp-watcher/config.json"
try:
    config = json.load(open(path))
except (OSError, ValueError):
    config = {"webhook_url": "", "webhook_type": "generic", "overrides": {}}
config["plugins_folder"] = sys.argv[1]
if sys.argv[2]:
    config["webhook_url"] = sys.argv[2]
if sys.argv[3]:
    config["webhook_type"] = sys.argv[3]
# its manifest names the author's repo, whose release is older than this build
config["ignore"] = sorted(set(config.get("ignore", [])) | {"mood-playlists"})
# its manifest points at navibeat.app instead of GitHub
config.setdefault("overrides", {}).setdefault("navibeat-mixes", "nenadjokic/navibeat-mixes")
json.dump(config, open(path, "w"), indent=2)
print("   plugin watcher: notifications " + ("to " + config["webhook_type"] if config["webhook_url"] else "only in the log (no webhook set)"))
WATCHERCONFIG
    chown -R "$RUN_USER:" /opt/ndp-watcher

    sed -e "s#@USER@#$RUN_USER#g" -e "s#@DATA@#$DATA#g" -e "s#@CONFIG@#$CONFIG#g" \
        -e "s#@ANALYZER_DIR@#$ANALYZER_DIR#g" -e "s#@MOOD_REPO@#$MOOD_REPO#g" \
        -e "s#@MOOD_BROKEN@#$MOOD_BROKEN_RELEASE#g" > /usr/local/bin/navidrome-plugin-update <<'UPDATE'
#!/bin/sh
# Daily plugin update for Navidrome, installed by setup-navidrome.sh. Run it by hand any time.
set -u
nd() { su -s /bin/sh @USER@ -c "cd '@DATA@' && navidrome -c '@CONFIG@' plugin $*"; }
enabled() { nd list 2>/dev/null | awk 'NR > 1 { for (i = 2; i <= NF; i++) if ($i == "true") { print $1; break } }'; }

# Navidrome switches a plugin off when its version changes, so remember which ones were on
was_on=$(enabled)

if [ -x /usr/local/bin/ndpm ]; then
    su -s /bin/sh @USER@ -c "/usr/local/bin/ndpm --config /etc/ndpm.yaml --once" || echo "ndpm reported a problem"
fi

# mood-playlists: the build from lidarr-someDL-plugin until its author releases one Navidrome 0.64
# accepts; then that release (and its analyzer), after which ndpm keeps it current
tag=$(curl -fsSL "https://api.github.com/repos/@MOOD_REPO@/releases/latest" 2>/dev/null | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)
if [ -n "$tag" ] && [ "$tag" != "@MOOD_BROKEN@" ] && [ -f "@DATA@/plugins/mood-playlists.ndp" ] \
    && ! grep -q "@MOOD_REPO@" /etc/ndpm.yaml; then
    if curl -fsSL "https://github.com/@MOOD_REPO@/releases/download/$tag/mood-playlists.ndp" -o "@DATA@/plugins/mood-playlists.ndp.tmp" \
        && curl -fsSL "https://raw.githubusercontent.com/@MOOD_REPO@/$tag/analyzer-service/app.py" -o "@ANALYZER_DIR@/app.py"; then
        chown @USER@: "@DATA@/plugins/mood-playlists.ndp.tmp" "@ANALYZER_DIR@/app.py"
        mv "@DATA@/plugins/mood-playlists.ndp.tmp" "@DATA@/plugins/mood-playlists.ndp"
        printf '  - repo: @MOOD_REPO@\n    filename: mood-playlists.ndp\n    slug: mood-playlists\n    include_prereleases: false\n' >> /etc/ndpm.yaml
        systemctl restart mood-analyzer 2>/dev/null || true
        echo "mood-playlists: moved to the author's release $tag"
    else
        rm -f "@DATA@/plugins/mood-playlists.ndp.tmp"
    fi
fi

sleep 10   # Plugins.AutoReload picks up replaced files
nd rescan >/dev/null 2>&1 || true
for id in $was_on; do
    nd enable "$id" >/dev/null 2>&1 || true
done
now_on=$(enabled)
for id in $was_on; do
    echo "$now_on" | grep -qx "$id" || echo "$id: could not switch it back on; see Settings > Plugins"
done

[ -f /opt/ndp-watcher/ndp_watcher.py ] && su -s /bin/sh @USER@ -c "python3 /opt/ndp-watcher/ndp_watcher.py"
exit 0
UPDATE
    chmod 755 /usr/local/bin/navidrome-plugin-update

    cat > /etc/systemd/system/navidrome-plugin-update.service <<'UNIT'
[Unit]
Description=Update Navidrome plugins from their GitHub releases
After=network-online.target navidrome.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/navidrome-plugin-update
UNIT
    cat > /etc/systemd/system/navidrome-plugin-update.timer <<'UNIT'
[Unit]
Description=Daily Navidrome plugin update

[Timer]
OnCalendar=*-*-* 04:30
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
UNIT
    systemctl daemon-reload
    systemctl enable --now navidrome-plugin-update.timer >/dev/null 2>&1
    echo "   daily at 04:30; log: journalctl -u navidrome-plugin-update; now: navidrome-plugin-update"
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
    echo "   listenbrainz-daily-playlist and navidrome-listenbrainz-plugin are installed but off:"
    echo "   configure and enable them in Settings > Plugins"
fi

systemctl is-active --quiet navidrome && echo "== Navidrome runs; old config kept as $CONFIG.bak" \
    || { echo "== Navidrome did NOT start; restore with: cp $CONFIG.bak $CONFIG && systemctl restart navidrome"; exit 1; }
