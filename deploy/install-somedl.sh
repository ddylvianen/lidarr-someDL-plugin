#!/bin/sh
# Installs SomeDL as a systemd service on Debian/Ubuntu, e.g. in the Proxmox LXC that runs Lidarr.
# Run as root:   sh install-somedl.sh [download folder]      (default: /data/somedl)
#
# - SomeDL goes in a Python venv in /opt/somedl, its settings in /var/lib/somedl
# - it runs as the same user as the 'lidarr' service, so Lidarr can move the files it downloads
# - it sets SomeDL's output template to one folder per album, which the Lidarr plugin needs
# Running it again updates SomeDL and keeps its settings.
set -eu

OUTPUT=${1:-/data/somedl}
SERVICE_URL=https://raw.githubusercontent.com/ddylvianen/lidarr-someDL-plugin/main/deploy/somedl.service

[ "$(id -u)" = 0 ] || { echo "Run as root"; exit 1; }

# Same user as Lidarr; override with SOMEDL_USER=... if Lidarr's service has another name
RUN_USER=${SOMEDL_USER:-$(systemctl show -p User --value lidarr 2>/dev/null || true)}
RUN_USER=${RUN_USER:-root}
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

if [ -f "$(dirname "$0")/somedl.service" ]; then
    cp "$(dirname "$0")/somedl.service" /etc/systemd/system/somedl.service
else
    curl -fsSL "$SERVICE_URL" -o /etc/systemd/system/somedl.service
fi
sed -i -e "s#@USER@#$RUN_USER#" -e "s#@GROUP@#$RUN_GROUP#" -e "s#@OUTPUT@#$OUTPUT#" /etc/systemd/system/somedl.service

systemctl daemon-reload
systemctl enable somedl >/dev/null 2>&1
systemctl restart somedl

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
