# Minebot: Minecraft building AI

The import/export milestone has passed automated checks and the user's visual check of the house in Minecraft. A shared block vocabulary and masked reconstruction exercises are now prepared. The first small neural network has trained on one project-generated house and exported a reconstruction. Its scores describe new masks of the same house; reconstruction of unfamiliar buildings remains to be tested.

## Shared local and cloud repository

This repository now contains the complete source project: GitHub discovery, Litematica parsing and
conversion, dataset preparation, optional training experiments, and the local reviewer. Cloud agents
must reuse the adapters described in [docs/ADAPTERS.md](docs/ADAPTERS.md). The bot commands and current
implementation status are in [docs/BOT.md](docs/BOT.md); the accepted bot plan is in [docs/PLAN.md](docs/PLAN.md).

For a fresh Linux/Codex setup with Python 3.12 and Node.js 22, run `bash scripts/setup-cloud.sh`.
It installs core/reviewer dependencies, builds the frontend with the existing lockfile, and runs
the portable tests. Windows setup remains `setup-reviewer.ps1` with a project `.venv`.
The GPU trainer is optional; cloud setup does not install PyTorch. Training tests report a skip when
PyTorch is absent, and installed-Minecraft-resource tests report skips when the resource cache is absent.

Downloaded schematics, reviewer/discovery databases, game resources, parsed datasets, experiment
checkpoints and exports stay local and are not in GitHub. The experiment results and artifact paths
below describe the original Windows workspace; those paths are empty placeholders in a fresh checkout.
The supplied tests generate their own schematic fixtures and do not require the four downloaded builds.

## Current setup

The local dataset reviewer is implemented. Run `start-reviewer.ps1` from PowerShell, then open `http://127.0.0.1:8765`. It uses a Python API, SQLite storage and a locally bundled React/Three.js interface. Style, function and feature classifications use persistent shared label lists, with explicit add-label controls. See [Reviewer usage](reviewer/README.md) for import, classification, approval, dataset export and local vanilla-resource setup. The viewer now uses vanilla JSON model geometry and block-state-dependent texture mapping for supported blocks, with explicit diagnostic fallbacks. It is not Minecraft's renderer.

The readiness and inventory tools use Python's standard library. The import/export pipeline uses nbtlib 2.0.4 to preserve typed NBT fields and Litemapy 0.11.0b0 as an independent block reader. They are installed in the project's `.venv`, which shares the bundled runtime's existing packages, including NumPy 2.3.5. `requirements.txt` records these versions. The environment depends on that bundled runtime remaining available; recreate it if the runtime is moved or replaced.

A bundled Python 3.12.14 runtime is available on this computer. The detected GPU is an NVIDIA GeForce RTX 3060 Laptop GPU with 6,144 MiB of video memory. PyTorch 2.9.1 with CUDA 12.8 is installed in `.venv`, and a CUDA tensor operation and the first training run passed. `requirements-training.txt` records the optional GPU training dependency, using the [official PyTorch installation index](https://pytorch.org/get-started/previous-versions/). The first run used batch size 2 and a 41778-parameter model; larger volumes and vocabularies still require profiling.

The working target is Minecraft Java 26.3 with Litematica 0.29.1 in a modded Modrinth profile, as reported by the user. Four downloaded Litematica builds have been confirmed by the user to load in that profile. All four have now passed parser and export comparisons. The mod loader and other installed mods have not been recorded.

Start with `.litematic` input to use these existing samples. Preserve each original file. Support for Sponge `.schem` can be added through a separate adapter to the same internal representation. The remaining defaults in `project.json` are proposed choices, not trained or measured capabilities.

## Project decisions

| Decision | Starting choice | Reason |
| --- | --- | --- |
| Building category | Small houses | Provides a focused first dataset |
| First learning task | Restore masked building sections | Gives a known target for each prediction |
| Training volume | Up to 32 by 32 by 32 blocks | Initial cap to profile on the available GPU |
| Output | Litematica schematic | Lets us inspect the result in Minecraft |
| User controls later | Footprint, height, palette and style | Matches the eventual design goal |
| Interiors | Preserve when present; not a first-generation requirement | Keeps the first experiment manageable |
| First experimental architecture | Small 3D convolutional model with block embeddings, mask and coordinates | Tests masked reconstruction with modest memory use |

Larger builds can be inspected, but must not be resized by stretching or shrinking their blocks. Either exclude them from the first training set or use explicit crops that retain the parent building identity.

## Steps to follow

1. Copy the four confirmed `.litematic` files into `data/raw_litematics/`, or begin with the smallest uncomplicated building. Record the download source and any known training permission in the annotation template. The original files can be inspected before deciding which qualify for training.
2. Run the starter inventory tool. It reports files, sizes, hashes and exact duplicates without modifying schematics. It does not parse schematic contents yet.
3. Implement and check import and export against that real file. Compare region positions, dimensions, every block state, and supported metadata. Load the exported file in Minecraft and inspect it next to the original. File bytes need not match because serialization and timestamps can change.
4. Gather 20 to 50 examples for a pipeline pilot. Record provenance, style, dimensions and visible problems. This is enough to debug data handling, not evidence that a broadly capable generator can be trained.
5. Define train, validation and test groups before making crops or rotated copies. Related buildings, duplicates, variants and all derivatives must stay in the same group. Reserve unseen buildings for evaluation.
6. Build simple baselines, then fit a small model to a few training examples as a wiring check. Follow with evaluation on held-out buildings. Proceed to wider training only after reviewing those results.

## Import and export results

The pipeline has decoded all four source files, saved the decoded arrays and preserved NBT to JSON, read that JSON back, and rebuilt new `.litematic` files. Every voxel index, full block-state palette, region geometry, auxiliary NBT field and root metadata value matched. Source file hashes were unchanged. Litemapy independently confirmed the coordinate and block-state interpretation for both original and exported files.

| Build | Dimensions in blocks | Voxel positions compared | Schematic format |
| --- | --- | --- | --- |
| Medieval house | 17 by 12 by 11 | 2244 | 6 |
| Castle | 41 by 42 by 53 | 91266 | 6 |
| use this | 32 by 25 by 31 | 24800 | 7 |
| Windmill | 17 by 27 by 17 | 7803 | 7 |

In total, 126113 voxel positions passed comparison. Four automated tests cover bit packing against a separate implementation, rejection of malformed arrays, negative coordinates and multiple regions with opaque metadata, and refusal to overwrite an existing export.

The user confirmed the exported house's blocks look correct in Minecraft. Other round-trip exports remain available for further visual checks. Compare files with the same placement rotation and mirroring, and use a test world if pasting the build.

Each round-trip export folder contains `inspection.json`, `verification.json`, `building.internal.json` and the exported schematic. The internal JSON remains an archival representation with region-local palettes. The new training preparation tools convert used states to standardized arrays. Unknown fields and entity/tick payloads are retained as typed NBT without being interpreted for learning. The edited exporter recalculates block counts, names and timestamps and clears stale previews. For now, edited exports require one region and empty entity, block-entity and pending-tick lists; more complex builds can still be inspected and copied unchanged.

## Completion exercises and first training result

`data/parsed_buildings/pilot-v1` contains the four standardized builds and a 706-state vocabulary. Each compressed array uses y,z,x axes, with full block-state properties retained. Its manifest leaves splits and family review unassigned. This inspection vocabulary comes from the entire pilot pool; future held-out evaluation must freeze a vocabulary based on training data and define how unseen states are handled.

`experiments/medieval-house-completion-v1` contains the real house's 72-state exercise, with 24 reproducible training masks and 4 distinct probe masks. Download usage remains unknown, so this exercise has not been trained. Its missing-section, neighbor-baseline and answer schematics have passed re-import and independent-reader checks.

`experiments/generated-house-completion-v1` contains a simple project-generated house with 6 block states. The network trained for 200 steps on its 24 training masks; its 4 probe masks were never used for gradient updates or checkpoint selection. The inputs replace hidden states with a separate mask token and include a mask flag. Only masked voxels contribute direct reconstruction loss. Air and non-air losses receive equal mean contributions when both are present.

| Method | Mean hidden block-state accuracy | Mean hidden non-air state accuracy |
| --- | --- | --- |
| Leave empty | 75.0 percent | 0.0 percent |
| Nearest visible neighbor | 81.7 percent | 44.0 percent |
| First neural model | 97.8 percent | 93.6 percent |

These are equal-weight averages across four probe masks of one generated house. The model can use normalized coordinates to learn that particular building. The result verifies the learning workflow and beats these baselines on this example; it does not establish architectural quality or generalization. PyTorch measured about 2.7 seconds for the training loop and 4.9 MiB of peak allocated tensor memory in this tiny run; that memory figure excludes GPU driver and runtime overhead and must not be used as a forecast for larger training.

The checkpoint was reloaded and reproduced the first prediction. The reconstruction was exported, re-imported and verified using the independent reader. Visible context was unchanged. The run folder contains `model.pt`, `training-report.json` and `reconstructed.litematic`.

Copy `experiments/generated-house-completion-v1/missing-section.litematic`, its `answer.litematic`, and `run-200/reconstructed.litematic` into the active profile's schematic folder. Compare them using identical placement settings. The missing-section and reconstruction files show the same first probe mask. Minecraft visual verification of these new files remains pending.

## Sources and component generators

The user reports that all four current builds are free downloads from Abfielder. Individual download-page links and training permission are still unknown; `data/annotations/sources.json` records the reported site and the user's other candidate sites. Free availability is recorded separately from training permission. [Abfielder's terms](https://www.abfielder.com/termsAndConditions) describe general permitted use and restrict automated collection, but do not explicitly name model training. No scraper has been implemented. The project currently trains its own generated example while specific usage terms remain unresolved.

The user's component-script suggestion is a useful next direction: write our own wall, window, floor and roof generators with recorded dimensions, palettes, seeds and component labels. Start with combinations of such components for abundant known-answer exercises, then evaluate separately on permitted human-designed buildings. Procedural examples can teach recurring geometry but may constrain variety; generating many similar variations does not replace independent evaluation. No third-party script code has been copied, and terrain examples remain separate from the house experiment.

## Data representation

Keep the source schematic unchanged. The lossless import representation must preserve region boundaries and offsets, Minecraft data version, block identifiers and properties, and any supported entity data. Unsupported fields must be reported explicitly; do not silently describe a lossy conversion as lossless.

The learning representation will use palette-indexed arrays with x, y and z axes, where y is vertical. Each palette entry identifies a complete block state, such as a stair's material, facing and half. Palette indices must map to a consistent vocabulary across builds; an index in one file does not necessarily mean the same block in another.

Air is a real target class. A masked or unknown position must use a separate mask so the model can distinguish empty space from information it has not been shown. Sparse storage may help archival size; dense local arrays remain a practical starting point for small crops.

Inspect multiple regions, negative region dimensions and overlapping regions before flattening them. Rotation requires updating directional block properties as well as coordinates. Defer rotation augmentation until that transformation has been verified. Do not change block materials during lossless import.

Detailed room and component annotations are optional for the pilot. Preserve style and provenance first; add expensive annotations only when an experiment needs them.

## Import and export library

The pipeline uses nbtlib for preservation and its own validated block packing adapter for schematic versions 6 and 7. It retains preview images, ticks and other auxiliary tags as typed NBT rather than relying on a higher-level library to recreate them. Litemapy provides an independent check of blocks and coordinates; its documentation notes limitations around preview images and pending ticks.

Sources: [Litemapy getting started](https://litemapy.readthedocs.io/en/latest/setup.html) and [Litematic format and coordinate systems](https://litemapy.readthedocs.io/en/latest/litematics.html).

## First model experiment

Start by profiling a small 3D convolutional model for masked reconstruction. This is a provisional engineering choice. The profile should measure memory and speed using the observed block-state vocabulary, because outputting scores for many states at every voxel can be expensive. A geometry-first head followed by material prediction is an option if the vocabulary is too large.

Generate masks only within the valid building extent and feed the mask explicitly. Mix missing cuboids with missing wall or roof sections when those sections can be identified. Compute the reconstruction loss on masked positions. Track geometry and material performance separately, and account for the predominance of air without removing it from the target.

Log dataset hashes, split assignments, random seed, configuration, code version, vocabulary and model checkpoint for each run. A successful fit to a few examples is a debugging result; held-out performance is the evidence of generalization.

## Evaluation and milestone gates

| Gate | Evidence required |
| --- | --- |
| Import and export | Block states and region geometry preserved; unsupported fields reported; exported file visibly loads in Minecraft |
| Dataset pilot | Provenance recorded; versions and vocabulary inspected; duplicate and family groups assigned |
| First experiment | Compare with leave-empty and simple neighbor-copy baselines on the same masks |
| Useful completion | Evaluate masked non-air precision and recall, block-state accuracy, and visual quality on unseen buildings |
| Larger generation | Agree on controls and compare generated designs with retrieval or procedural examples |

Report air and non-air scores separately: a model that outputs air everywhere can look good on an overall accuracy metric. For visual review, inspect the same hidden section across the original, baseline and model prediction without revealing which prediction is which.

Validation distinguishes file correctness, block-specific placement behavior and design preferences. Unsupported falling blocks, door halves and directional states need specific checks. Floating ordinary blocks are not automatically invalid. Roof quality, room access and lighting depend on the intended build and should initially be review criteria.

## Starter commands

Open a PowerShell terminal and enter the project folder explicitly:

```powershell
Set-Location 'C:\Users\Family\Documents\Codex\2026-10-11\i-x20\outputs\minecraft-building-ai'
.\.venv\Scripts\python.exe tools/project.py doctor
.\.venv\Scripts\python.exe tools/project.py inventory
.\.venv\Scripts\python.exe tools/litematic_pipeline.py inspect 'data/raw_litematics/medieval-house-from-abfielder.litematic'
```

To repeat the conversion, use a new output folder name; existing output folders are preserved:

```powershell
.\.venv\Scripts\python.exe tools/litematic_pipeline.py roundtrip 'data/raw_litematics/medieval-house-from-abfielder.litematic' --output-dir exports/medieval-house-roundtrip-2
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`inspect` prints the dimensions, palette usage and preservation notes without writing files. `roundtrip` writes a new internal archive, exported schematic and reports inside the specified exports subfolder.

To run another copy of the verified generated-house experiment, choose a new output folder:

```powershell
Set-Location 'C:\Users\Family\Documents\Codex\2026-10-11\i-x20\outputs\minecraft-building-ai'
.\.venv\Scripts\python.exe tools/train_completion.py --exercise experiments/generated-house-completion-v1 --output-dir experiments/generated-house-completion-v1/run-200-2 --steps 200 --device cuda
```

Preparation commands for future new output folders:

```powershell
.\.venv\Scripts\python.exe tools/completion_data.py prepare-pilot --output-dir data/parsed_buildings/pilot-v2
.\.venv\Scripts\python.exe tools/completion_data.py prepare-exercise --synthetic --output-dir experiments/generated-house-completion-v2
.\.venv\Scripts\python.exe tools/completion_data.py prepare-exercise --source 'data/raw_litematics/medieval-house-from-abfielder.litematic' --output-dir experiments/medieval-house-completion-v2
```

The trainer accepts project-generated data, or an exercise with `training_permission` set to `allowed` and a recorded `permission_evidence` reference. Do not mark a downloaded exercise allowed solely because it was free. Dataset preparation does not grant permission. Outputs refuse to replace existing folders or files.

`doctor` checks the starter configuration and directory layout. `inventory` identifies files by relative path and SHA-256 hash; identical hashes form one exact-duplicate group. It does not identify visually similar variants or establish usage permission.

For each build, copy `data/annotations/build.template.json` into a new JSON file and fill in its known values. Keep unknown values as null. Never treat an embedded author name as proof of permission.
