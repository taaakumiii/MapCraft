# Third-party notices

MapCraft's own code is MIT-licensed. That license does not replace any third-party license below.

| Component / data | Terms and attribution |
|---|---|
| Arnis v3.4.0, Louis Erbkamm and contributors | Apache-2.0. Unmodified official Windows executable. See `licenses/Arnis-LICENSE.txt`, `Arnis-NOTICE.txt`, `Arnis-UPSTREAM-NOTICE.txt`, and the complete upstream data/asset credits in `Arnis-UPSTREAM-CREDITS.js`. https://github.com/louis-e/arnis/tree/v3.4.0 |
| Upstream minimap, kirbycope/minimap-bedrock | MIT. Keep `native_minimap/LICENSE-upstream.txt`, `CREDITS-upstream.md`, and `UPSTREAM.json`. MapCraft changes the geographic UI, pack identity and gameplay features. |
| Amulet LevelDB 1.0.7 / Amulet NBT 2.1.6 | **Amulet Team License 1.0.0**, not MIT/Apache. Required Notice: Copyright Amulet Team. (https://www.amuletmc.com/). Full terms: `licenses/amulet_leveldb-1.0.7-LICENSE.txt`, `licenses/amulet_nbt-2.1.6-LICENSE.txt` and the original wheel licenses. These include permitted-purpose and noncompete provisions; do not describe the complete package as unrestricted commercial software. |
| Python 3.12.10 embedded | Python Software Foundation License; full original `engine/national-runtime/LICENSE.txt` and provenance are included with the binary distribution. |
| PyInstaller | GPL with its bootloader exception; see the bundled PyInstaller license. This does not change MapCraft's source license. |
| Pillow, NumPy, Pyosmium/libosmium/protozero, Shapely/GEOS, Requests and other runtime packages | Their original licenses and bundled dependency notices are retained in the runtime's `dist-info` and library directories. See `requirements-runtime.txt` for pinned versions. |
| OpenStreetMap | © OpenStreetMap contributors, ODbL 1.0. https://www.openstreetmap.org/copyright / https://opendatacommons.org/licenses/odbl/1-0/ |
| Geofabrik Japan extract and extent | OSM-derived data, Geofabrik. https://download.geofabrik.de/asia/japan.html . No full Japan PBF or local indexed database is included in the source or Creator ZIP. |
| Ministry of Land, Infrastructure, Transport and Tourism | National Land Numerical Information Administrative Zones N03, 2026-01-01 / CC BY 4.0. Land polygons are projected to the common grid and buffered approximately 500m for coast coverage, clipped with OSM administrative boundaries. Downloaded prefecture ZIPs are cached by the creator; generated worlds retain source URLs and hashes in `mapcraft_boundary_source.json`. https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-2026.html |
| Geological Survey of Japan, AIST | Seamless Digital Geological Map of Japan (1:200,000), V2, processed by MapCraft. https://gsj-seamless.jp/seamless/v2/api/1.3/ . See `licenses/Geology-data-NOTICE.txt`. |
| Geospatial Information Authority of Japan | Artificial landform classification, processed by MapCraft. https://www.gsi.go.jp/bousaichiri/lfc_index.html |
| Japan Meteorological Agency | 111 active-volcano representative positions, processed from the List of Active Volcanoes in Japan (retrieved 2026-11-08). https://www.data.jma.go.jp/vois/data/filing/bulletin/catalog/appendix/v_active.html . Lava geometry and mineral distributions are MapCraft gameplay approximations, not JMA underground data. |
| Ministry of Internal Affairs and Communications / e-Stat | Municipal population from the Basic Resident Register, total Japanese and foreign residents, as of 2026-01-01; published 2026-07-29. Processed table 26-03. https://www.e-stat.go.jp/stat-search/files?stat_infid=000040479049 . Population-to-villager ratios and area apportionment are MapCraft approximations. See `licenses/Ecology-data-NOTICE.txt`. |
| Mojang Bedrock Samples item identifiers | Identifier-derived catalogue only; no original Mojang models, sounds or textures are distributed by this catalogue. See `licenses/Item-catalogue-NOTICE.txt`. |
| Mojang Bedrock Samples entity identifiers / saved state | Vanilla entity identifiers and component-group names checked against official samples. Native mob/health/equipment/portal/chest serialization is MapCraft code; no original entity JSON, artwork or models are bundled. https://github.com/Mojang/bedrock-samples |

The frozen GUI also includes Shapely/GEOS and NumPy for offline water cartography. Their wheel license trees are bundled inside the executable under `licenses/GUI-shapely` and `licenses/GUI-numpy`; build versions are pinned in `requirements-build.txt`.

Generated worlds may use additional sources through Arnis options such as terrain, building supplementation and tree data. Preserve the engine's source/asset notices and the generated pack credits when distributing worlds.

ODbL-derived map images and vector/boundary datasets are not relicensed under MIT. Keep the OSM source data and attribution supplied with an add-on. This project is not an official Minecraft product and is not approved by or associated with Mojang or Microsoft.

The Amulet license text is included to make its conditions visible; this document is a component inventory, not a separate permission from its authors. Check the applicable original license before commercial or competitive distribution/use.

Bathymetry: NOAA NCEI (2022), ETOPO 2022 15 Arc-Second Global Relief Model, DOI 10.25921/fd45-gt74. https://www.ncei.noaa.gov/products/etopo-global-relief-model . Numeric subsets are retrieved when requested and cached locally; bathymetry is interpolated and depth-limited for gameplay. Source TIFFs are not bundled in the Creator ZIP.
