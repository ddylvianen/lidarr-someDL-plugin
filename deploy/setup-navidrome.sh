#!/bin/sh
# Points an installed Navidrome (Debian package or tarball with a systemd unit) at the Lidarr library
# and sets the options this setup relies on. Run as root in the Navidrome LXC:
#
#   sh setup-navidrome.sh [library folder]      default: /data/navidrome when it holds music
#
# Keeps a backup of the old config (navidrome.toml.bak) and any Last.fm keys already in it.
set -eu

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

# Artist images, biographies, similar artists
Agents = "deezer,lastfm"
$( [ -n "$LFM_KEY" ] && echo "LastFM.ApiKey = \"$LFM_KEY\"" || echo '# LastFM.ApiKey = ""   (https://www.last.fm/api/account/create)' )
$( [ -n "$LFM_SECRET" ] && echo "LastFM.Secret = \"$LFM_SECRET\"" || echo '# LastFM.Secret = ""' )
ListenBrainz.Enabled = true

CoverArtPriority = "cover.*, folder.*, front.*, embedded, external"
LyricsPriority   = ".lrc,.txt,embedded"
EnableSharing    = true

Plugins.Enabled = true
Plugins.Folder  = "$DATA/plugins"
TOML
chown "$RUN_USER:" "$DATA/plugins"

systemctl restart navidrome
sleep 5
echo "== log after restart"
journalctl -u navidrome -n 60 --no-pager -o cat | grep -iE "error|warn|unknown|invalid|music folder|library" || echo "   no errors"
systemctl is-active --quiet navidrome && echo "== Navidrome runs; old config kept as $CONFIG.bak" \
    || { echo "== Navidrome did NOT start; restore with: cp $CONFIG.bak $CONFIG && systemctl restart navidrome"; exit 1; }
