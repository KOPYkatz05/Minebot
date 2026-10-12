# Local Minecraft dataset reviewer

Inspect independently loaded original and converted Litematica files, record review decisions, correct classifications, and assemble named training datasets. Everything runs locally. Files are not uploaded to an external service.

## Start and stop

In PowerShell, run this from the project folder:

```powershell
.\start-reviewer.ps1
```

The script also works by absolute path from another directory. Open http://127.0.0.1:8765 in a regular browser. Keep the terminal running; press Ctrl+C there to stop the service. The app uses a fixed port and accepts loopback hosts only. Starting it does not install software or automatically import files.

Dependencies and the frontend are already prepared on this computer. For a fresh setup, create a Python 3.12 environment in `.venv`, install Node.js and pnpm, then run:

```powershell
.\setup-reviewer.ps1
```

Setup explicitly downloads pinned Python and frontend dependencies and builds the local frontend. The frontend lockfile makes subsequent installs reproducible. After setup, the reviewer needs no internet connection. PyTorch is not required to run the reviewer.

## Review a build

1. Choose **Import files**, drop `.litematic` files on the import area, or choose **Import project builds**. **Import generated demo** provides a project-owned example for testing training eligibility and exports.
2. Watch the **Processing** page. Imports run in one worker. A corrupt file fails independently and can be retried. Files larger than 64 MiB, decompressed NBT larger than 256 MiB, and builds over 5 million total voxels are rejected. This release supports schematic format versions 6 and 7.
3. Select a build in the **Review queue**. The details panel shows measured dimensions and counts separately from editable human labels. Style, function and feature labels come from separate shared lists. Check existing labels to apply them; use **Add** to create a label in that category and apply it. Labels are trimmed and matched case-insensitively, while the first saved spelling is retained. Existing annotations seed the lists during database migration.
4. Inspect the original and converted views. Cameras are linked by default. Slice by world y-coordinate, select regions separately, and click a block to inspect its coordinates, name, and properties. Missing, added, and changed blocks use red, blue, and amber markers. Exact counts include all differences; overlays are limited to 10000 markers per region and the detailed list to 200 entries.
5. **Fly here** enters spectator movement: WASD moves, Space/E moves up, Shift/Q moves down, and Escape exits. If the browser denies pointer lock, drag on the canvas to look while flying. Leaving the canvas focus exits fallback navigation. Reset view restores the orbit camera.
6. Save classifications, notes, redstone presence and family grouping. Expand **Provenance & permitted usage** to record the source, license, attribution, repository revision and permission evidence. A free download alone is not marked permitted automatically.
7. Choose **Approve**, **Reject**, or **Skip**. Approve requires a current passing conversion. Skip retains a deferred item in the queue. A/R/S shortcuts work outside text entry and spectator mode. Successful decisions advance to the next queue item. Library decisions can be changed later, with history retained.

The viewer uses vanilla JSON block models from your own Minecraft installation. **Vanilla textures** is enabled by default when local resources are present; disable it for deterministic material colors without changing the supported model shapes. The two independently loaded files use the same resource resolver. Inheritance, variant conditions and all matching multipart conditions select model elements and their materials. Unsupported blocks use labeled diagnostic cubes; missing textures use diagnostic colors.

Supported model features include top/bottom/double slabs; straight, inner and outer stairs with facing and half states; hinged/open doors and trapdoors; connected fences, walls and panes; cropped and default face UVs; face texture rotation; block-state X/Y rotation and UV locking; and element rotation/rescaling. Geometry is baked into material-grouped triangles and instanced across matching states. Coordinates and selection indices remain independent of geometry. Grass overlays and cutout faces are retained from the model definitions. Camera linking, slicing, selection and difference colors still apply to both views.

This is still a resource-model preview, not the game's renderer. It does not simulate world ambient occlusion, neighbor-face culling, waterlogged overlays, animated textures, biome colors or dynamic block-specific tints. Foliage tint is a fixed approximation; other model tints use neutral colors with a warning. Animated texture strips use a static first frame. Weighted alternatives use a stable first model rather than Minecraft's position-seeded selection. Model-level `transformation` definitions are explicitly unsupported. Transparency sorting and lighting remain approximate. **Model preview limitations** lists per-material exceptions, and selecting a block reveals the original and converted model references. Approval still records `diagnostic_data_review`, not verification using Minecraft's renderer. Block states and auxiliary metadata are compared exactly in the backend; equivalent palettes are not differences. Geometry ambiguity or missing region counterparts is explicitly reported, not silently flattened.

### Special blocks and version-wide coverage

Static adapters now connect vanilla entity textures for ordinary, trapped, ender and copper chests (including double-half states), all 16 standing/wall banner colors, standing/wall skull and head families, and all 17 shulker box colors with six facing directions. Water, lava and bubble columns use static textured surfaces with approximate level height; end portals/gateways use a static texture instead of the game's shader. These adapters are explicitly labeled previews: chest/shulker animations, banner pattern NBT, custom player skins, fluid neighbor heights/flow and some special silhouettes/atlas seams are not faithfully reproduced. Player heads use local Steve textures; the app does not fetch external skin URLs. Banner base colors are shown without decoration patterns. Native entity-atlas images are not mistaken for vertically stacked animated block textures.

Render-only compatibility aliases map `grass` to `short_grass`, `chain` to `iron_chain`, and `grass_path` to `dirt_path`. Source IDs, properties, comparisons, reviews and training exports remain unchanged. Each alias is explained in the preview limitations. Air, light, barrier and structure-void states are intentionally invisible in normal viewing; differences at those locations still receive diagnostic markers. Unknown mod blocks remain diagnostic fallbacks rather than being silently relabeled vanilla.

Choose **Vanilla coverage** in the navigation to inspect a searchable, version-bound report. It audits one representative state per blockstate file in the installed client, distinguishing native JSON geometry, static special adapters, labeled textured proxies and intentionally invisible blocks. This is **not** verification of every state combination or complete Minecraft rendering. The current 26.3 inventory has 1288 definitions: 1192 native model previews, 79 static special previews, 11 textured proxies and 6 intentionally invisible blocks, with no unaccounted representative entries. The remaining proxies are conduit, decorated pot, moving piston and the copper-golem statue variants; they have textures but not faithful special geometry/behavior. Search for `textured_proxy` to inspect them.

To regenerate the machine-readable report after importing a different client, run from the project folder:

```powershell
.\.venv\Scripts\python.exe -m reviewer.coverage
```

The report is written to ignored `data/reviewer/vanilla/coverage.json`; `/api/vanilla/coverage` exposes the same audit. The installed client resources are the version-specific reference. Mojang's [26.1 special-model changes](https://www.minecraft.net/en-us/article/minecraft-java-edition-26-1) explain why special block rendering requires separate handling rather than particle-textured cubes.

Implementation references are the cached **Minecraft 26.3 client blockstates and models** (version/hash recorded by the importer), [Fabric's block-model documentation](https://docs.fabricmc.net/develop/blocks/block-models), and [Forge's Java model-system documentation](https://docs.minecraftforge.net/en/1.21.x/resources/client/models/). The installed client is authoritative for this project's specific model and variant definitions; documentation for Bedrock models is not used.

## Local vanilla textures

Minecraft 26.3 block resources are already installed for this computer's reviewer. They were copied from the installed Modrinth client, without changing the game or adding resources to training exports. Resources are stored only in the ignored `data/reviewer/vanilla/` cache, not bundled in frontend code or committed to the repository. No resource pack, CDN, or download service is used.

On another computer, or to change the Minecraft resource version, stop the reviewer, run this command from the project folder with the path to your **own installed vanilla client JAR**, then restart the reviewer:

```powershell
.\.venv\Scripts\python.exe -m reviewer.assets 'C:\path\to\installed\minecraft-client.jar'
.\start-reviewer.ps1
```

The importer copies allowlisted vanilla texture PNGs (including entity atlases), model JSON, item definitions and block-state JSON into a content-addressed cache. It records the client version, SHA-256 hash and resource schema, and rejects oversized resource archives. The server exposes allowlisted texture identifiers, never arbitrary filesystem paths. Older caches need reimporting for entity texture support. An absent cache does not prevent normal diagnostic reviewing. Mod-provided materials and custom resource packs are not supported yet. Changing this rendering cache does not change conversion fingerprints, review approvals or immutable dataset versions.

Use **Replace converted file** to compare a separately converted `.litematic`. The original remains unchanged, a new conversion record is created, and old approval no longer applies. **Reprocess** regenerates or rechecks the verified counterpart. Changed artifacts or converter code make approval stale. If a managed original was changed externally, restore it or import the changed file as a new build; the reviewer will not silently change its recorded identity.

## Library and datasets

The **Library** includes pending, approved, rejected and deferred builds. Filter by text, classifications, redstone presence, usage status, archive status and largest dimension. Archiving hides a build from active views and blocks future training exports without deleting originals, history or existing dataset versions. Archived builds can be restored.

Create a collection on **Datasets**. Include builds individually, or use **Add filtered builds…** to review the exact selection before confirming. Adding a build changes membership only; it does not approve it. Removing a build from one collection does not change another. Classifications never automatically change membership. Collections can be renamed, described, archived and restored.

Dataset export defaults to excluding confirmed redstone builds; the setting is editable. Excluded members remain visible as blockers, not silently omitted. A build is training eligible only when its current conversion passes, its review is approved, its permitted usage has evidence, its type is classified, and redstone presence is confirmed. Snapshot export additionally requires confirmed family grouping and an assigned split.

Assign train, validation or test to a **family**, not an individual derivative. All members of that family share the split within a dataset. Changing a family label makes its grouping unconfirmed and the new group's split initially unassigned. Group duplicates, crops, rotations, recolorings and language variants together before export. Different dataset experiments may deliberately assign groups differently; comparisons between experiments need their own evaluation design.

**Preview export** lists every member and blocking reason. **Export version** is enabled only for a ready preview, and the backend rechecks readiness and artifact hashes at export time. A version copies both original and converted artifacts and freezes membership, labels, provenance, converter fingerprints, reviews and splits in a JSON manifest. Download its ZIP or inspect its manifest. Later label, membership or archival changes do not rewrite an existing version.

## Storage and architecture

Managed sources, conversion caches, the SQLite database, database backups and immutable snapshots live under `data/reviewer/`, which is excluded from version control. Back up this folder to preserve reviewer state. Its originals and conversions are content-checked; never manually edit them. Failed snapshot staging directories are retained for diagnosis and are not published as dataset versions.

FastAPI/Uvicorn serves the frontend and `/api` endpoints at one local origin. Write requests from unrelated browser origins are rejected. File access uses record IDs, not arbitrary user-supplied paths. SQLite enables foreign keys and uses numbered schema migrations; future migrations must back up the existing database first. Running jobs become recoverable interrupted jobs after an unexpected stop. The shared label vocabulary is stored locally in SQLite; build annotations can only use labels already registered in their matching category.

The frontend uses React, TypeScript, Vite and Three.js, with local assets and no CDN. The `ViewerAdapter` boundary supports scene loading, camera state, slicing, difference highlighting, selection and disposal. Scene payloads retain regions, coordinates, full block states and stable voxel indices for a future Minecraft-derived geometry adapter. Minecraft export workers, automatic classification, other import formats, remote accounts and training controls are not implemented in this release. Discovery includes local MineBot pause/resume, paginated candidate search, collection/validation/delivery retries, dismiss/restore, stage progress, attempt history and storage usage. Received handoffs retain pinned metadata, evidence downloads and links to review; see docs/BOT.md for local configuration and stage commands.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The reviewer adds regression checks for palette-independent comparisons, missing blocks and property changes, negative dimensions and multiple regions, duplicate imports, failed and interrupted jobs, review persistence, independent dataset membership, stale approvals, provenance and redstone gating, group splits, immutable snapshots and local request restrictions.

Browser acceptance used the four existing source files and a generated demo. The demo was classified, approved, included in a collection, assigned a training split and exported. Linked camera state, shared slicing and block inspection were checked. Replacing the house's conversion with the existing missing-section exercise produced 25 missing blocks and disabled approval; reprocessing restored the verified counterpart. Screenshot capture was unavailable in the test browser, so final visual layout inspection remains a user check. Minecraft-faithful rendering remains a future milestone.

Vanilla-resource tests additionally verify inherited face maps, directional log variants, Minecraft 26.3 texture descriptors, unknown-material fallbacks, missing caches, resource-path restrictions and local-origin restrictions. Texture loading was checked in both browser panels: the castle loaded 205 textures per panel with no image-load errors; the house textured 669 of its 671 non-air voxels. Switching back to colors, shared layer slicing (81 voxels at the lowest house layer), synchronized cameras and block selection were checked. Screenshot capture remained unavailable, so exact visual fidelity has not been asserted.

The subsequent geometry milestone adds portable hand-authored tests for partial bounds, UV rectangles/rotation/locking, stair variants, multipart connections, element rotation/rescaling, outward winding and explicit fallbacks. Optional installed-resource integration tests check all 40 oak stair states, three slab types, doors, trapdoors, fences, panes and walls against the cached client; they skip when local assets are absent. The real house loaded 668 modeled voxels and 12614 triangles per panel, and block selection reported the north-facing spruce stair's model and full properties. These are functional/data checks, not a claim of pixel-identical Minecraft rendering.

Special-renderer tests check all 19 requested materials, 32 standing/wall banner colors, head families, chest halves/materials, all shulker colors/facings, fluid levels, entity atlas crops, invisible states, non-mutating aliases and the complete representative-state inventory. Browser checks confirmed the coverage search/proxy filter and all 26784 castle voxels textured in both panels, with 247 loaded texture images per panel and no image-load errors. Screenshot capture remains unavailable, so atlas orientation, special silhouettes and visual fidelity still require comparison with Minecraft.
