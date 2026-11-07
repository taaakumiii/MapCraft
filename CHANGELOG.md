# Changelog

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
