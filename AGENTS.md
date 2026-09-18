# Repository rules

- This repository contains only self-authored Codex plugins and supporting documentation.
- Never vendor third-party skills, models, runtimes, logs, caches, credentials, browser data, payment images, or personal files.
- Before publishing any plugin change, run `scripts/publish-custom-plugins.ps1 -DryRun` and resolve every failure.
- Only plugins explicitly listed in `config/publish-allowlist.json` may be synchronized.
- Do not weaken secret scanning, path restrictions, manifest validation, or the clean-worktree requirement to force a publish.
- A successful validation may be followed by `scripts/publish-custom-plugins.ps1 -Push`.

