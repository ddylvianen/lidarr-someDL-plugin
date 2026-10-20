#!/bin/sh
# Installs the nightly FLAC job (tools/music_flac.py) on Debian/Ubuntu, in the LXC that has the music.
# Run as root:   sh install-music-flac.sh /data/music
# Optional, so Lidarr notices renamed files right away: put LIDARR_URL and LIDARR_API_KEY in
# /etc/default/music-flac. Running it again updates the script and keeps that file.
set -eu

LIBRARY=${1:?usage: sh install-music-flac.sh /path/to/music}
REPO_RAW=https://raw.githubusercontent.com/ddylvianen/lidarr-someDL-plugin/main
HERE=$(dirname "$0")

[ "$(id -u)" = 0 ] || { echo "Run as root"; exit 1; }
[ -d "$LIBRARY" ] || { echo "$LIBRARY does not exist"; exit 1; }

fetch() {
    if [ -f "$HERE/../$1" ]; then cp "$HERE/../$1" "$2"; else curl -fsSL "$REPO_RAW/$1" -o "$2"; fi
}

apt-get update -qq
apt-get install -y -qq python3 python3-mutagen ffmpeg flac optipng libjpeg-turbo-progs curl >/dev/null

mkdir -p /opt/music-flac /var/lib/music-flac
fetch tools/music_flac.py /opt/music-flac/music_flac.py
fetch deploy/music-flac.service /etc/systemd/system/music-flac.service
fetch deploy/music-flac.timer /etc/systemd/system/music-flac.timer
sed -i "s#@LIBRARY@#$LIBRARY#" /etc/systemd/system/music-flac.service

[ -f /etc/default/music-flac ] || cat > /etc/default/music-flac <<'DEFAULTS'
# Fill these in so Lidarr rescans folders where a file was converted to FLAC (new file name)
#LIDARR_URL=http://192.168.x.x:8686
#LIDARR_API_KEY=
DEFAULTS

systemctl daemon-reload
systemctl enable --now music-flac.timer >/dev/null 2>&1

echo "Nightly at 03:30: $LIBRARY to FLAC + recompress. Start now: systemctl start music-flac"
echo "Preview first:  python3 /opt/music-flac/music_flac.py $LIBRARY --dry-run --min-age 0"
echo "Cover/padding gain, changes nothing: python3 /opt/music-flac/music_flac.py $LIBRARY --measure --min-age 0"
echo "Log:            journalctl -u music-flac"
