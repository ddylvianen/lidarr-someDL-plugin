#!/bin/sh
# Builds the plugin in a throwaway .NET SDK container (no local .NET needed).
# Output: Lidarr.Plugin.SomeDL.net8.0.zip in this folder.
set -e
cd "$(dirname "$0")"

PLUGIN_VERSION=${PLUGIN_VERSION:-0.2.0.0}
# Lidarr only loads a plugin built against a Lidarr.Core no newer than itself, so the submodule is
# stamped with the oldest Lidarr the plugin supports (same trick as the GitHub workflow).
MINIMUM_LIDARR_VERSION=${MINIMUM_LIDARR_VERSION:-3.1.5.5066}

git submodule update --init src/Lidarr 2>/dev/null || true
# The version stamp below must reach Lidarr.Core, which an incremental build would not rebuild for it
rm -rf src/Lidarr/_temp src/Lidarr/_output src/Lidarr/_plugins
sed -i -e "s/<AssemblyVersion>[0-9.*]\+<\/AssemblyVersion>/<AssemblyVersion>$PLUGIN_VERSION<\/AssemblyVersion>/" src/Directory.Build.props
sed -i -e "s/<AssemblyVersion>[0-9.*]\+<\/AssemblyVersion>/<AssemblyVersion>$MINIMUM_LIDARR_VERSION<\/AssemblyVersion>/" src/Lidarr/src/Directory.Build.props

docker run --rm -e DOTNET_CLI_TELEMETRY_OPTOUT=1 -v somedl-plugin-nuget:/root/.nuget \
  -v "$PWD":/w -w /w mcr.microsoft.com/dotnet/sdk:8.0 sh -c "
    dotnet publish src/Lidarr.Plugin.SomeDL.sln -c Release -f net8.0 -p:NuGetAudit=false;
    status=\$?; chown -R $(id -u):$(id -g) /w; exit \$status"
