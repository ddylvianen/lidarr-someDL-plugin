#!/bin/sh
# Updates Lidarr plugins without Lidarr's task queue, where plugin installs wait until no other task
# runs (which never happens while Lidarr refreshes a large library). Run as root where Lidarr runs:
#
#   sh update-plugin.sh                     this plugin (ddylvianen/lidarr-someDL-plugin)
#   sh update-plugin.sh TypNull/Tubifarry   any plugin, by its GitHub owner/repo
#   sh update-plugin.sh --all               every installed plugin (Lidarr keeps them in plugins/<owner>/<repo>)
#   add --check to only report what would be updated
#
# Takes the newest release that is not a pre-release (or the newest at all when a repo only has those, as
# this plugin does), installs it where Lidarr's own installer would, and restarts Lidarr once at the end.
# Lidarr's data folder is found by its config.xml; set LIDARR_DATA=/path to point at it yourself.
set -eu

CHECK=""
TARGETS=""
for arg in "$@"; do
    case "$arg" in
        --check) CHECK=1 ;;
        --all) TARGETS="--all" ;;
        */*) TARGETS="$TARGETS $arg" ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done
[ -n "$TARGETS" ] || TARGETS="ddylvianen/lidarr-someDL-plugin"

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

if [ "$TARGETS" = "--all" ]; then
    TARGETS=$(cd "$DATA/plugins" 2>/dev/null && for d in */*/; do [ -d "$d" ] && printf '%s ' "${d%/}"; done)
    [ -n "$TARGETS" ] || { echo "No plugins installed in $DATA/plugins"; exit 0; }
fi

for tool in unzip python3; do
    command -v $tool >/dev/null || { apt-get update -qq && apt-get install -y -qq $tool >/dev/null; }
done
OWNER_IDS=$(stat -c '%u:%g' "$DATA/config.xml")
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
CHANGED=0

update() {
    repo=$1
    target="$DATA/plugins/$repo"

    if [ -f "$target/.release" ]; then
        installed=$(cat "$target/.release")
    elif ls "$target"/Lidarr.Plugin.*.dll >/dev/null 2>&1; then
        installed="installed by Lidarr (its version shows under System > Plugins)"
    else
        installed="not installed"
    fi

    if ! curl -fsSL "https://api.github.com/repos/$repo/releases?per_page=20" -o "$WORK/releases.json"; then
        echo "$repo: could not read its releases on GitHub"; return 0
    fi
    # Newest release with a net8.0 plugin zip, stable ones first
    python3 - "$WORK/releases.json" > "$WORK/pick" <<'PY'
import json, sys
releases = [r for r in json.load(open(sys.argv[1])) if not r.get("draft")]
def asset(r):
    return next((a["browser_download_url"] for a in r.get("assets", []) if a["name"].endswith(".zip") and "net8" in a["name"]), None)
usable = [r for r in releases if asset(r)]
stable = [r for r in usable if not r.get("prerelease")]
pick = (stable or usable or [None])[0]
if pick:
    print(pick["tag_name"], asset(pick))
PY
    tag=""; url=""
    read -r tag url < "$WORK/pick" || true
    if [ -z "$tag" ]; then
        echo "$repo: no release with a plugin zip found"; return 0
    fi

    echo "$repo: installed: $installed, newest: $tag"
    [ "$installed" = "$tag" ] && return 0
    [ -n "$CHECK" ] && return 0

    rm -rf "$WORK/plugin"
    curl -fsSL "$url" -o "$WORK/plugin.zip"
    unzip -q -o "$WORK/plugin.zip" -d "$WORK/plugin"
    if ! ls "$WORK/plugin"/Lidarr.Plugin.*.dll >/dev/null 2>&1; then
        echo "$repo: the release zip has no Lidarr plugin in it, skipped"; return 0
    fi
    mkdir -p "$target"
    cp -r "$WORK/plugin/." "$target/"
    echo "$tag" > "$target/.release"
    chown -R "$OWNER_IDS" "$DATA/plugins"
    echo "$repo: installed $tag"
    CHANGED=1
}

for repo in $TARGETS; do
    update "$repo"
done

if [ "$CHANGED" = 1 ]; then
    if systemctl list-unit-files 2>/dev/null | grep -q '^lidarr\.service'; then
        systemctl restart lidarr
        echo "Lidarr restarted."
    else
        echo "Restart Lidarr yourself to load the update(s)."
    fi
fi
