#!/bin/sh
# Installs tools/slskd_unstick.py in the slskd LXC: every 30 minutes it cancels downloads that sit in
# another user's queue for more than HOURS (default 3), so Lidarr fails them and grabs another source.
# Run as root:
#
#   sh install-slskd-unstick.sh [hours] [slskd api key]
#
# The API key is read from slskd's config when not given. Starts with a dry run of what it would cancel.
set -eu

HOURS=${1:-3}
KEY=${2:-}
REPO_RAW=https://raw.githubusercontent.com/ddylvianen/lidarr-someDL-plugin/main
HERE=$(dirname "$0")
ENV=/etc/slskd-unstick.env

[ "$(id -u)" = 0 ] || { echo "Run as root"; exit 1; }
command -v python3 >/dev/null || { apt-get update -qq && apt-get install -y -qq python3 >/dev/null; }

if [ -z "$KEY" ]; then
    # web.authentication.api_keys.<name>.key in slskd.yml
    for config in /etc/slskd/slskd.yml /var/lib/slskd/slskd.yml /opt/slskd/slskd.yml /app/slskd.yml \
        "$(getent passwd slskd | cut -d: -f6)/.local/share/slskd/slskd.yml" /root/.local/share/slskd/slskd.yml; do
        [ -f "$config" ] || continue
        KEY=$(awk '/api_keys:/ {in_keys = 1; next} in_keys && /^[^ #]/ {in_keys = 0} in_keys && $1 == "key:" {print $2; exit}' "$config" | tr -d '"'"'")
        [ -n "$KEY" ] && { echo "API key read from $config"; break; }
    done
fi
[ -n "$KEY" ] || { echo "No API key found; pass it: sh $0 $HOURS <api key> (the one Lidarr's Slskd settings use)"; exit 1; }

mkdir -p /opt/slskd-unstick
if [ -f "$HERE/../tools/slskd_unstick.py" ]; then
    cp "$HERE/../tools/slskd_unstick.py" /opt/slskd-unstick/
else
    curl -fsSL "$REPO_RAW/tools/slskd_unstick.py" -o /opt/slskd-unstick/slskd_unstick.py
fi

umask 077
printf 'SLSKD_URL=http://127.0.0.1:5030\nSLSKD_API_KEY=%s\nUNSTICK_HOURS=%s\n' "$KEY" "$HOURS" > "$ENV"
umask 022

cat > /etc/systemd/system/slskd-unstick.service <<EOF
[Unit]
Description=Cancel slskd downloads stuck in other users' queues
After=network-online.target slskd.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -u /opt/slskd-unstick/slskd_unstick.py --env-file $ENV
EOF
cat > /etc/systemd/system/slskd-unstick.timer <<'EOF'
[Unit]
Description=Cancel stuck slskd downloads every 30 minutes

[Timer]
OnBootSec=10min
OnUnitActiveSec=30min

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now slskd-unstick.timer >/dev/null 2>&1

echo "== dry run: what it would cancel now"
python3 -u /opt/slskd-unstick/slskd_unstick.py --env-file "$ENV" --dry-run || true
echo "== installed: every 30 minutes, cancels what is queued remotely for more than $HOURS hours"
echo "   now for real: systemctl start slskd-unstick; log: journalctl -u slskd-unstick"
