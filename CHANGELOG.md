# Changelog

## 0.15.0-preview

- Independent per-floor chest draws with a configurable 0–100% chance (25% default), at most one chest per usable floor, including rooms below thick cobbled roofs.
- Backfill old private creator tiles from source data while preserving existing generator chest inventories and beds; retry journals retain floor identities.
- Optional bounded process pool for geological calculations, with a single LevelDB writer and identical native block results.
- Region phases hold future tiles, safely hand over at a tile boundary, export the completed region and stop while retaining the national project.

## 0.14.0-preview

- Bake ordinary Bedrock chests, balanced regional inventories and both bed halves into creator outputs before play; no furniture add-on is required.
- Optional add-on-free ordinary export, retaining native furniture and data credits.
- National tile ownership keeps both bed halves in merged chunks; older private build projects are backfilled before export.
- Creator-side write-ahead journal prevents refilling or respawning furniture on retry. Baked packs no longer run deferred placement plans.
- Native LevelDB readback and engine-to-national merge checks; in-game opening, sleeping and multiplayer acceptance remain unverified.

## 0.13.0-preview

- Portable Creator distribution, per-world Player packages, source-only repository and reproducible build scripts.
- Explicit dependency/data notices, checksums and a distribution file manifest.
- Per-region deferred chest/bed plans for large Bedrock worlds.
- Safe national prerequisites: missing index probes do not create SQLite files; buttons follow actual workflow state.
- Optional unattended creator job with Kanto-first ordering, byte-budget reservation, stage retries and saved progress.
- Real-map HUD frame alignment, Tokyo Station initial spawn, geological material adaptation, balanced regional adventure loot.

This is a preview release. Complete nationwide generation, large-world import performance and every supported in-game combination have not been validated.
