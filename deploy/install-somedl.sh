#!/bin/sh
# Installs SomeDL as a systemd service on Debian/Ubuntu, e.g. in the Proxmox LXC that runs Lidarr.
# Run as root:   sh install-somedl.sh [download folder] [library folder]
#   download folder: where SomeDL saves (default /data/somedl)
#   library folder:  optional; also installs a timer that moves albums downloaded through SomeDL's
#                    WebUI into this folder (SOMEDL_MOVER_LINK=1 to hardlink instead of move)
# Lidarr in another LXC: SOMEDL_UID=<uid Lidarr runs as there> sh install-somedl.sh /data/somedl
#
# - SomeDL goes in a Python venv in /opt/somedl, its settings in /var/lib/somedl
# - it runs as the same user as the 'lidarr' service, so Lidarr can move the files it downloads
# - it sets SomeDL's output template to one folder per album, which the Lidarr plugin needs
# Running it again updates SomeDL and keeps its settings.
set -eu

OUTPUT=${1:-/data/somedl}
LIBRARY=${2:-}
REPO_RAW=https://raw.githubusercontent.com/ddylvianen/lidarr-someDL-plugin/main
HERE=$(dirname "$0")

# Copies a file of this repo from next to this script, or downloads it when the script was fetched alone
fetch() {
    if [ -f "$HERE/../$1" ]; then cp "$HERE/../$1" "$2"; else curl -fsSL "$REPO_RAW/$1" -o "$2"; fi
}

[ "$(id -u)" = 0 ] || { echo "Run as root"; exit 1; }

# Same user as Lidarr, so Lidarr may move the files. When Lidarr runs in another LXC, pass the UID
# Lidarr runs as there (SOMEDL_UID=1000); a user with that UID is then used or created here.
if [ -n "${SOMEDL_UID:-}" ]; then
    RUN_USER=$(getent passwd "$SOMEDL_UID" | cut -d: -f1)
    if [ -z "$RUN_USER" ]; then
        RUN_USER=somedl
        useradd --system --uid "$SOMEDL_UID" --user-group --home-dir /var/lib/somedl --no-create-home "$RUN_USER"
    fi
else
    RUN_USER=${SOMEDL_USER:-$(systemctl show -p User --value lidarr 2>/dev/null || true)}
    RUN_USER=${RUN_USER:-root}
fi
RUN_GROUP=$(id -gn "$RUN_USER")
echo "SomeDL will run as $RUN_USER:$RUN_GROUP and download into $OUTPUT"

apt-get update -qq
apt-get install -y -qq python3 python3-venv ffmpeg curl unzip ca-certificates >/dev/null

# yt-dlp needs a JavaScript runtime for YouTube's signature challenges
if ! command -v deno >/dev/null; then
    curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y --no-modify-path >/dev/null
fi

[ -x /opt/somedl/bin/python ] || python3 -m venv /opt/somedl
/opt/somedl/bin/pip install --quiet --disable-pip-version-check -U somedl
/opt/somedl/bin/pip install --quiet --disable-pip-version-check -U --pre "yt-dlp[default]"

mkdir -p /var/lib/somedl "$OUTPUT"
# The service updates yt-dlp on start, so its user must be able to write the venv
chown -R "$RUN_USER:$RUN_GROUP" /opt/somedl /var/lib/somedl "$OUTPUT"

fetch deploy/somedl.service /etc/systemd/system/somedl.service

# 'somedl-seed': MusicBrainz seeding (tools/somedl_mb_seed.py), pointed at this SomeDL by default
fetch tools/somedl_mb_seed.py /opt/somedl/somedl_mb_seed.py
cat > /usr/local/bin/somedl-seed <<'WRAPPER'
#!/bin/sh
export SOMEDL_URL="${SOMEDL_URL:-http://127.0.0.1:5000}"
exec /usr/bin/python3 -I /opt/somedl/somedl_mb_seed.py "$@"
WRAPPER
chmod 755 /usr/local/bin/somedl-seed
sed -i -e "s#@USER@#$RUN_USER#" -e "s#@GROUP@#$RUN_GROUP#" -e "s#@OUTPUT@#$OUTPUT#" /etc/systemd/system/somedl.service

# extradl: Bandcamp/SoundCloud for the plugin's second indexer and download client
fetch tools/extradl.py /opt/somedl/extradl.py
fetch deploy/extradl.service /etc/systemd/system/extradl.service
sed -i -e "s#@USER@#$RUN_USER#" -e "s#@GROUP@#$RUN_GROUP#" -e "s#@OUTPUT@#$OUTPUT#" /etc/systemd/system/extradl.service
mkdir -p "$OUTPUT/extradl"
chown "$RUN_USER:$RUN_GROUP" "$OUTPUT/extradl"

if [ -n "$LIBRARY" ]; then
    [ -d "$LIBRARY" ] || { echo "Library folder $LIBRARY does not exist"; exit 1; }
    MODE=""
    [ "${SOMEDL_MOVER_LINK:-0}" = 1 ] && MODE="--link"
    fetch tools/somedl_mover.py /opt/somedl/somedl_mover.py
    fetch deploy/somedl-mover.service /etc/systemd/system/somedl-mover.service
    fetch deploy/somedl-mover.timer /etc/systemd/system/somedl-mover.timer
    sed -i -e "s#@USER@#$RUN_USER#" -e "s#@GROUP@#$RUN_GROUP#" -e "s#@OUTPUT@#$OUTPUT#" \
        -e "s#@LIBRARY@#$LIBRARY#" -e "s# @MODE@#${MODE:+ $MODE}#" /etc/systemd/system/somedl-mover.service
fi

systemctl daemon-reload
systemctl enable somedl >/dev/null 2>&1
systemctl restart somedl
systemctl enable extradl >/dev/null 2>&1
systemctl restart extradl
if [ -n "$LIBRARY" ]; then
    systemctl enable --now somedl-mover.timer >/dev/null 2>&1
fi

printf "Waiting for SomeDL"
i=0
until curl -sf http://127.0.0.1:5000/get-version >/dev/null; do
    i=$((i + 1))
    [ $i -lt 60 ] || { echo; echo "SomeDL did not start; see: journalctl -u somedl"; exit 1; }
    printf "."
    sleep 3
done
echo

# One folder per album, otherwise Lidarr cannot import (the plugin's Test checks this too)
curl -sf -X POST http://127.0.0.1:5000/settings-apply -H 'Content-Type: application/json' \
    -d '{"settings":{"download":{"output":"{album_artist}/{album}/{track_pos} - {song}"}},"update_active":true}' >/dev/null

echo "SomeDL $(curl -s http://127.0.0.1:5000/get-version | sed 's/.*"v":"\([^"]*\)".*/\1/') runs on port 5000."
echo "In Lidarr use host 127.0.0.1 (same machine) or this machine's IP, port 5000."
echo "Bandcamp/SoundCloud (extradl) runs on port 5050: add it in Lidarr as indexer and download client too."
[ -z "$LIBRARY" ] || echo "WebUI downloads move to $LIBRARY after 30 idle minutes (log: journalctl -u somedl-mover)."
echo "Add a YouTube Music album to MusicBrainz: somedl-seed \"artist album\" (somedl-seed --help)"
