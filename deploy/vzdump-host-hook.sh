#!/bin/sh
# vzdump hook: after every backup job, also save the Proxmox host's own configuration next to the
# guest backups, which vzdump itself never includes (/etc with fstab, network, storage.cfg, /root with
# scripts, cron jobs, and the cluster database that holds every guest's config).
#
# Install on the Proxmox host, for every backup job:
#   sh vzdump-host-hook.sh --install
# Keeps the newest KEEP (default 8) host archives per backup storage. Restore: see the README in each
# archive (tar tzf ... README-restore.txt).
set -eu

HOOK=/usr/local/bin/vzdump-host-hook.sh
KEEP=${KEEP:-8}

if [ "${1:-}" = "--install" ]; then
    install -m 755 "$0" "$HOOK"
    if grep -q '^script:' /etc/vzdump.conf 2>/dev/null; then
        sed -i "s#^script:.*#script: $HOOK#" /etc/vzdump.conf
    else
        echo "script: $HOOK" >> /etc/vzdump.conf
    fi
    echo "Installed: every backup job now also saves the host config (script: $HOOK in /etc/vzdump.conf)."
    echo "Try it now without waiting for a job: $HOOK job-end"
    exit 0
fi

# vzdump calls the hook for every phase; only the end of a whole job matters here
[ "${1:-}" = "job-end" ] || exit 0

# The dump folder of the storage this job wrote to; a manual run falls back to local
TARGET=${DUMPDIR:-}
if [ -z "$TARGET" ]; then
    STORE=${STOREID:-local}
    BASE=$(pvesh get "/storage/$STORE" --output-format json 2>/dev/null | sed -n 's/.*"path" *: *"\([^"]*\)".*/\1/p')
    TARGET=${BASE:-/var/lib/vz}/dump
fi
mkdir -p "$TARGET"

ARCHIVE="$TARGET/pve-host-$(hostname)-$(date +%Y_%m_%d-%H_%M_%S).tar.gz"
README=$(mktemp)
cat > "$README" <<TXT
Proxmox host configuration of $(hostname), $(date), Proxmox $(pveversion 2>/dev/null | cut -d/ -f2)

Restore after reinstalling Proxmox (same version, same hostname):
  - copy back what you need from etc/ and root/ (fstab, network/interfaces, sysctl.d, cron.*, scripts)
  - guest configs: stop pve-cluster, copy var/lib/pve-cluster/config.db back, start pve-cluster;
    or simply restore each guest from its own vzdump backup, which carries its config too
TXT

# The cluster database is copied through sqlite's backup, consistent while Proxmox runs; /etc/pve is
# that same database mounted, so it is left out of the tar of /etc
DB=$(mktemp -d)
if command -v sqlite3 >/dev/null; then
    sqlite3 /var/lib/pve-cluster/config.db ".backup '$DB/config.db'"
else
    cp /var/lib/pve-cluster/config.db "$DB/config.db"
fi

tar czf "$ARCHIVE" --exclude=etc/pve --ignore-failed-read \
    -C / etc root usr/local \
    -C "$DB" config.db \
    -C "$(dirname "$README")" "$(basename "$README")" \
    --transform "s#^config.db#var/lib/pve-cluster/config.db#;s#^$(basename "$README")#README-restore.txt#" 2>/dev/null || true
rm -rf "$DB" "$README"
echo "host config saved: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"

# Retention: newest KEEP host archives in this folder
ls -1t "$TARGET"/pve-host-"$(hostname)"-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
