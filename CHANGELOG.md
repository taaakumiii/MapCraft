# Changelog

## 0.19.2-preview

- Render offline minimap seas from directed OSM coastlines, and use a clear blue for seas, rivers, lakes and reservoirs. Preserve islands in water multipolygons and leave incomplete coastlines unfilled.
- Invalidate older navigation imagery on export without regenerating terrain; native ocean worlds also show blue across the synthetic sea between mapped islands.

## 0.19.1-preview

- Enable boss-location beacons by default while preserving an explicit off setting. Boss generation remains separately configurable.
- Reuse existing wildlife and boss plans when only the beacon flag changes; no OSM re-extraction is needed for that toggle.
- Keep animal writes batched with beacons enabled, flushing beacon blocks before publishing their completed journal entries.

## 0.19.0-preview

- Add cached NOAA ETOPO 2022 bathymetry correction for existing open water; preserve land, structures, tunnels and mapped freshwater, skip small pools, and cap depth at 60 blocks.
- Increase new-job wildlife defaults to 200%, use 128m habitat cells, and select the dominant habitat by usable area so small streams do not suppress livestock.
- New GUI jobs enable villagers at 250 residents per villager, prioritize low floors and optionally use safe residential yards. Preserve saved older flags and ratios.
- Buffer actor writes using pending intents, retry transient Windows JSON locks, and validate old shard preconditions after LevelDB file rotation.

- Add an opt-in boss-location beacon setting, off by default for existing and new settings. Save a vanilla beacon and its one-layer iron base near successfully placed bosses or End entrances, without additional mods/packs.
- Preserve buildings, reward chests and blocked sky columns; skip beacon placement when there is no suitable nearby location. Journal beacons independently so retries neither respawn bosses nor replace mined beacon/base blocks. Beacons remain after boss defeat.
- Add restartable, per-tile furniture difference databases with boundary checks and sorted, verified world DB rebuilding. Keep the original database as a backup and reuse completed tile calculations after interruption.
- Accelerate snapshot range reads and OSM multipolygon ring joining; preserve native furniture records and inventories in equivalence tests.

## 0.18.0-preview

- Bake habitat-appropriate vanilla animals into the world, with an adjustable density and no ecosystem script/mod/pack dependency.
- Optional area-ranked vanilla bosses and native rare-item reward chests at mapped Shinto/Buddhist/cemetery sites, including small shrines. Building footprints can provide an explicitly approximate area fallback.
- Rank thresholds: Piglin Brute <2,000m², Elder Guardian <10,000m², Warden <50,000m², Wither <200,000m²; larger sites receive a vanilla End portal for the normal End dragon fight. Direct bosses receive area-scaled health. Elder Guardians receive a small water basin.
- Persist vanilla component-group state for adult animals and brutes without the overworld conversion sensor; keep standard entity AI and vanilla combat behavior. No custom HP bars, loot locks or regional respawn scripts.
- Journal stable entity/reward identities to prevent generator retries from duplicating or refilling output. Ordinary chest balance is unchanged; dedicated boss rewards use their own area-tiered table.
- Navigation is now opt-in for newly created ordinary/national projects; pack-free outputs retain animals, bosses, rewards, villagers and furniture. Existing saved project options remain unchanged.

## 0.17.0-preview

- Optional native vanilla villagers in residential interiors, apportioned from municipal population and generated area. Default off; adjustable 100–100,000 real residents per villager (1,000 when enabled).
- Bundle 2026-01-01 municipal resident-registry population (Japanese and foreign residents); report unmatched municipalities and unavailable safe interiors instead of guessing.
- Bake actor records and chunk digests into the world, preserving existing actors and retry identities. No villager add-on is required.
- Use JMA's 111 active-volcano representative positions for sealed deep lava/magma pockets and modest nearby mineral enrichment. Positions are real; underground geometry and ore frequencies are fictional gameplay models.
- Bias nearby chest common/uncommon mineral loot, retaining the same rare/relic gate and maximum one rare item per chest. CPU and GPU paths share identical volcano decoration.
- Remove obsolete local validation/build copies and historical screenshots; retain referenced minimap PNG assets, generated worlds and live jobs.

## 0.16.1-preview

- Remove the launcher batch file from source and distributions; launch MapCraft.exe directly.

## 0.16.0-preview

- Optional NVIDIA driver-only geological compute, with no required GPU runtime package or CUDA Toolkit installation.
- Compare warm CPU/GPU execution and exact native bytes; automatically use the faster path and fall back to CPU when GPU initialization or a later batch fails.
- CPU-only setting and adaptive CPU worker count; native tests cover missing GPUs, mid-batch errors, existing blocks and real RTX 4060 Ti output equivalence.

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
