#!/bin/sh
# Installs the nightly "Niet leuk" clean-up (tools/navidrome_dislikes.py) in the Lidarr LXC. Run as root:
#
#   sh install-dislikes.sh <navidrome url> <navidrome user> [unmonitor|delete]
#   e.g. sh install-dislikes.sh http://192.168.2.80:4533 dylan
#
# Asks for the Navidrome password, keeps everything in /etc/navidrome-dislikes.env (root only), runs
# every night at 05:15 and once now as a dry run, so you see what it would do before anything changes.
# Default mode is unmonitor; delete removes disliked albums and their files. Rerun to change settings.
set -eu

URL=${1:-}
USER_NAME=${2:-}
MODE=${3:-unmonitor}
[ -n "$URL" ] && [ -n "$USER_NAME" ] || { sed -n '2,10p' "$0"; exit 1; }
case "$MODE" in unmonitor|delete) ;; *) echo "Mode must be unmonitor or delete"; exit 1 ;; esac
[ "$(id -u)" = 0 ] || { echo "Run as root"; exit 1; }

REPO_RAW=https://raw.githubusercontent.com/ddylvianen/lidarr-someDL-plugin/main
HERE=$(dirname "$0")
ENV=/etc/navidrome-dislikes.env

command -v python3 >/dev/null || { apt-get update -qq && apt-get install -y -qq python3 >/dev/null; }
mkdir -p /opt/navidrome-dislikes
if [ -f "$HERE/../tools/navidrome_dislikes.py" ]; then
    cp "$HERE/../tools/navidrome_dislikes.py" /opt/navidrome-dislikes/
else
    curl -fsSL "$REPO_RAW/tools/navidrome_dislikes.py" -o /opt/navidrome-dislikes/navidrome_dislikes.py
fi

# Keep the password from a previous run unless a new one is typed
OLD_PASSWORD=$(sed -n 's/^NAVIDROME_PASSWORD=//p' "$ENV" 2>/dev/null || true)
printf "Navidrome password for %s%s: " "$USER_NAME" "${OLD_PASSWORD:+ (Enter keeps the current one)}"
stty -echo 2>/dev/null || true
read -r PASSWORD || true
stty echo 2>/dev/null || true
echo
PASSWORD=${PASSWORD:-$OLD_PASSWORD}
[ -n "$PASSWORD" ] || { echo "No password given"; exit 1; }

PLAYLIST=$(sed -n 's/^DISLIKE_PLAYLIST=//p' "$ENV" 2>/dev/null || true)
umask 077
cat > "$ENV" <<EOF
NAVIDROME_URL=$URL
NAVIDROME_USER=$USER_NAME
NAVIDROME_PASSWORD=$PASSWORD
DISLIKE_PLAYLIST=${PLAYLIST:-Niet leuk}
DISLIKE_MODE=$MODE
EOF
umask 022

cat > /etc/systemd/system/navidrome-dislikes.service <<EOF
[Unit]
Description=Unmonitor or delete in Lidarr what is in Navidrome's "Niet leuk" playlist
After=network-online.target lidarr.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -u /opt/navidrome-dislikes/navidrome_dislikes.py --env-file $ENV
EOF
cat > /etc/systemd/system/navidrome-dislikes.timer <<'EOF'
[Unit]
Description=Nightly "Niet leuk" clean-up

[Timer]
OnCalendar=*-*-* 05:15
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now navidrome-dislikes.timer >/dev/null 2>&1

echo "== dry run (nothing is changed)"
python3 -u /opt/navidrome-dislikes/navidrome_dislikes.py --env-file "$ENV" --dry-run || true
echo "== installed: runs every night at 05:15 in '$MODE' mode"
echo "   now for real: systemctl start navidrome-dislikes; log: journalctl -u navidrome-dislikes"
