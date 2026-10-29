# Workspace maintenance

Run commands from the repository root. Source directories remain at their
original paths so the running service, firmware build, and model tools keep
working. Feature guides now live under `docs/`.

## Tracked

Track application/firmware source, browser assets (including `headend/web/dist`),
tests, scripts, lock files, model manifests, small required embedded wake/chime
assets, documentation, and vendored source with its licenses. Vendored files
retain their original bytes through `.gitattributes` for provenance checking.

## Local only

- `headend/data/`: accounts, password hashes, device keys, pages and settings.
- `headend/recordings/` and `artifacts/`: captured audio, transcripts, screenshots,
  serial logs, private network observations and test results.
- `headend/models/`: downloaded speech weights and caches.
- `headend/.venv/`, `headend/.browser-test/`: installed packages and browser state.
- `firmware/build/`, `firmware/managed_components/`, `firmware/sdkconfig*`:
  generated build state; retain `firmware/sdkconfig.defaults` in Git.
- `backups/`: recovery firmware and older local working copies.
- Training datasets, work folders, checkpoints and generated results.
- `.env*`, local config, key files and per-machine editor settings.

The cleanup preserved existing local data and backups. Do not delete active
runtime directories to tidy the repository. A fresh clone creates its own
accounts and downloads its own model weights using the setup commands.
Diagnostic reports referenced in historical notes are intentionally local-only.

## Before sharing changes

Use `git status --short` and `git diff --cached --stat` to review what will be
committed. Do not force-add excluded recordings, databases, logs, credentials,
or model caches. Keep sensitive configuration in the ignored local locations.
Use a private repository until you choose the intended distribution/license.
The source checkout is not a backup of device settings, recordings or accounts;
back those up separately if needed.
