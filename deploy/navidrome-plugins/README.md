# Navidrome plugins built here

## mood-playlists.ndp

The mood-playlists plugin by Craig Lusher (https://github.com/craiglush/navidrome-mood-plugin, GPL-3.0,
see `mood-playlists.LICENSE`), built from the fix for Navidrome 0.64 that is not released yet:

- source: https://github.com/WernerVdM97/navidrome-mood-plugin, branch `fix/navidrome-0.64`,
  commit `7fd4862ea9f20a3b741f6c49cc4d74a71557d4a6` (upstream pull request #9)
- built with TinyGo (`tinygo build -opt=2 -scheduler=none -no-debug -target wasip1 -buildmode=c-shared`),
  packaged as `plugin.wasm` + `manifest.json`, as the project's Makefile does

The released v0.2.0 is refused by Navidrome 0.64 ("'scheduler' permission requires plugin to export
'nd_scheduler_callback' function"). `deploy/setup-navidrome.sh` installs this build instead; switch back to
the upstream release once it includes the fix.
