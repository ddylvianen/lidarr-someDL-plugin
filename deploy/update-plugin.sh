#!/bin/sh
# Updates the plugin without Lidarr's task queue, where plugin installs wait until no other task runs
# (which never happens while Lidarr refreshes a large library). Run as root in the Lidarr LXC/VM:
#
#   sh update-plugin.sh            install the newest release if it is not installed yet, restart Lidarr
#   sh update-plugin.sh --check    only say whether there is a newer release
#
# Lidarr's data folder is found by its config.xml; set LIDARR_DATA=/path to point at it yourself.
set -eu

REPO=ddylvianen/lidarr-someDL-plugin
CHECK_ONLY=${1:-}

DATA=${LIDARR_DATA:-}
if [ -z "$DATA" ]; then
    for candidate in /var/lib/lidarr /config /opt/lidarr-data; do
        [ -f "$candidate/config.xml" ] && DATA=$candidate && break
    done
fi
if [ -z "$DATA" ]; then
    DATA=$(dirname "$(find / -xdev -name config.xml -path '*idarr*' 2>/dev/null | head -1)")
fi
[ -f "$DATA/config.xml" ] || { echo "Lidarr's config.xml not found; set LIDARR_DATA=/path/to/it"; exit 1; }

# Where Lidarr's own plugin install puts it, so both ways of installing stay interchangeable
TARGET="$DATA/plugins/ddylvianen/lidarr-someDL-plugin"
INSTALLED=$(cat "$TARGET/.release" 2>/dev/null || echo none)

# Newest release, pre-releases included (that is how the workflow publishes them)
RELEASES=$(curl -fsSL "https://api.github.com/repos/$REPO/releases?per_page=5")
TAG=$(printf '%s' "$RELEASES" | grep -o '"tag_name": *"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
ASSET=$(printf '%s' "$RELEASES" | grep -o '"browser_download_url": *"[^"]*net8\.0\.zip"' | head -1 | sed 's/.*"\(https[^"]*\)"$/\1/')
[ -n "$TAG" ] && [ -n "$ASSET" ] || { echo "Could not find a release on GitHub"; exit 1; }

echo "Installed: $INSTALLED, newest: $TAG"
if [ "$INSTALLED" = "$TAG" ]; then
    echo "Up to date."
    exit 0
fi
[ "$CHECK_ONLY" = "--check" ] && exit 0

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
curl -fsSL "$ASSET" -o "$WORK/plugin.zip"
command -v unzip >/dev/null || { apt-get update -qq && apt-get install -y -qq unzip >/dev/null; }
unzip -q -o "$WORK/plugin.zip" -d "$WORK/plugin"
[ -f "$WORK/plugin/Lidarr.Plugin.SomeDL.dll" ] || { echo "The release zip has no plugin in it"; exit 1; }

OWNER=$(stat -c '%u:%g' "$DATA/config.xml")
mkdir -p "$TARGET"
cp "$WORK/plugin/"* "$TARGET/"
echo "$TAG" > "$TARGET/.release"
chown -R "$OWNER" "$DATA/plugins"
echo "Installed $TAG in $TARGET"

if systemctl list-unit-files 2>/dev/null | grep -q '^lidarr\.service'; then
    systemctl restart lidarr
    echo "Lidarr restarted."
else
    echo "Restart Lidarr yourself to load it."
fi
