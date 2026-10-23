#!/bin/sh
# Points an installed Navidrome (Debian package or tarball with a systemd unit) at the Lidarr library
# and sets the options this setup relies on. Run as root in the Navidrome LXC:
#
#   sh setup-navidrome.sh [library folder]      default: /data/navidrome when it holds music
#
# Keeps a backup of the old config (navidrome.toml.bak) and any Last.fm keys already in it.
#
# Also installs (or updates, on a rerun) these plugins from their GitHub releases:
#   enabled:  apple-music (artist images/bios), coverartarchive (MusicBrainz covers), nd-lyrics (lyrics)
#   disabled until configured in Settings > Plugins: listenbrainz-daily-playlist, navidrome-ntfy,
#             discord-rich-presence
# NAVIDROME_PLUGINS=0 skips them.
set -eu

# repo  asset  enable-right-away
PLUGINS="
navidrome/apple-music-plugin apple-music.ndp yes
sunsetroute1/navidrome-coverartarchive-plugin coverartarchive.ndp yes
J0R6IT0/navidrome-lyrics-plugin nd-lyrics.ndp yes
kgarner7/navidrome-listenbrainz-daily-playlist listenbrainz-daily-playlist.ndp no
andykelk/navidrome-ntfy-plugin navidrome-ntfy.ndp no
navidrome/discord-rich-presence-plugin discord-rich-presence.ndp no
"

CONFIG=$(systemctl cat navidrome 2>/dev/null | sed -n 's/.*"-c" "\([^"]*\)".*/\1/p' | head -1)
CONFIG=${CONFIG:-/etc/navidrome/navidrome.toml}
RUN_USER=$(systemctl show -p User --value navidrome 2>/dev/null || true)
RUN_USER=${RUN_USER:-navidrome}
DATA=$(sed -n 's/^DataFolder *= *"\(.*\)"/\1/p' "$CONFIG" 2>/dev/null | head -1)
DATA=${DATA:-/var/lib/navidrome}
OLD_MUSIC=$(sed -n 's/^MusicFolder *= *"\(.*\)"/\1/p' "$CONFIG" 2>/dev/null | head -1)

echo "== Navidrome $(navidrome --version 2>/dev/null || echo '(version unknown)'), config $CONFIG, runs as $RUN_USER"

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
    echo "$PLUGINS" | while read -r repo asset enable; do
        [ -n "$repo" ] || continue
        if curl -fsSL "https://github.com/$repo/releases/latest/download/$asset" -o "$DATA/plugins/$asset.part"; then
            mv "$DATA/plugins/$asset.part" "$DATA/plugins/$asset"
            echo "   ${asset%.ndp}: installed (latest release of $repo)"
        else
            rm -f "$DATA/plugins/$asset.part"
            echo "   ${asset%.ndp}: download FAILED ($repo)"
        fi
    done
    chown -R "$RUN_USER:" "$DATA/plugins"
fi

systemctl restart navidrome
sleep 5
echo "== log after restart"
journalctl -u navidrome -n 80 --no-pager -o cat | grep -iE "error|warn|unknown|invalid|music folder|library|plugin" | grep -viE "not found in deezer|usable artist page" | tail -20 || echo "   no errors"
if [ "${NAVIDROME_PLUGINS:-1}" != 0 ] && systemctl is-active --quiet navidrome; then
    nd() { su -s /bin/sh "$RUN_USER" -c "cd '$DATA' && navidrome -c '$CONFIG' plugin $*"; }
    nd rescan >/dev/null 2>&1 || true
    echo "$PLUGINS" | while read -r repo asset enable; do
        [ "$enable" = yes ] && [ -f "$DATA/plugins/$asset" ] || continue
        id=${asset%.ndp}
        nd enable "$id" >/dev/null 2>&1 && echo "   $id: enabled" \
            || echo "   $id: enable it in Settings > Plugins"
    done
    nd list 2>/dev/null || true
    echo "   the others are installed but off: configure and enable them in Settings > Plugins"
fi

systemctl is-active --quiet navidrome && echo "== Navidrome runs; old config kept as $CONFIG.bak" \
    || { echo "== Navidrome did NOT start; restore with: cp $CONFIG.bak $CONFIG && systemctl restart navidrome"; exit 1; }
