# CLAUDE.md — 3sx HD Asset Pipeline

## Release v1 scope (2026-09-10)

The official first release packs ONLY stages (including bonus backgrounds)
and the 20 fighters. UI, HUD, menus, portraits, logos and Opening remain
experimental because of unresolved problems. Preserve all scripts, notes and
local generated data; do not delete the experimental work. `build_hd_test.py`
now writes `work/release-v1` using explicit stage and character allowlists.
`work/hd-test` retains the earlier experimental bundle. Never broaden release
packaging by globbing all repacks or sidecars. README is the current release
workflow; `docs/experimental-workflows.md` preserves the earlier mixed workflow.

## Project context
This repo works with [crowded-street/3sx](https://github.com/crowded-street/3sx), a native
PC/PS2-decomp-based port of *Street Fighter III: 3rd Strike*. 3sx requires the user to supply
their own legally-owned `SF33RD.AFS` (from a real PS2 disc/ISO or the Anniversary Collection).
It ships **no** Capcom assets itself.

Goal: build tooling that extracts textures/sprites from a **locally-owned** `SF33RD.AFS`,
upscales/cleans them up (aiming for a smoother, less-pixelated, hand-drawn-adjacent look),
and repacks them as an HD texture pack that 3sx can load — the same pattern used by fan HD
texture packs for N64/PS1 games (e.g. MK64-Reloaded), where only the *new* derivative art is
distributed, never the original files.

## Hard rules for Claude Code in this repo
- **Never** commit, upload, paste, or transmit `SF33RD.AFS` or any extracted sprite/texture
  data anywhere (no GitHub pushes, no gists, no pastebins, no external API calls containing
  asset bytes). Treat the AFS and everything extracted from it as local-only, gitignored.
- **Never** attempt to bypass CPS3/PS2 encryption or DRM beyond what 3sx itself already does
  to load a legally-supplied file — this project only touches the already-decrypted PS2 asset
  container, not arcade ROM protections.
- Do not add code whose purpose is to redistribute or re-share original Capcom assets in any
  form (raw, re-encoded, or "lightly edited"). Only *newly generated/upscaled derivative*
  textures, produced by the pipeline, may end up in an output/ or texture-pack/ folder — and
  even then, that folder should stay gitignored unless the user explicitly wants to publish a
  clean pack that still requires the user's own AFS to function (no bundled original assets).
- If unsure whether something crosses into asset redistribution, stop and ask the user rather
  than assuming.

## Repository entry points

[README.md](README.md) is the user-facing guide: disclaimer, dependencies and a
step-by-step build for somebody who has only their own `SF33RD.AFS` and a 3SX
checkout. `setup.sh` checks the toolchain; `requirements.txt` pins Pillow.
`patches/3sx-hd-textures.patch` is the engine change against upstream
`513380f9`, regenerate it with `(cd 3sx && git diff HEAD)` after editing the
engine. Keep all three in sync when the pipeline changes.

## Current state (2026-09-09)

The local test build runs 4x stages and all 20 fighters at 4x (`pl00`-`pl14`
and `pl16`-`pl20`; there is no `pl15`): 24,760 exported frames packed into
219,943 indexed HD tiles across 20 sidecars. Character geometry, anchors and
cache slot counts stay unchanged; the two player caches use 1024x1024 pages
when a 4x sidecar is enabled.

```sh
./work/hd-test/run.sh           # HD characters + all 21 stage/bonus replacements
./work/hd-test/run.sh --stock   # original character pixels, same HD stages
python3 tools/build_hd_test.py  # rebuild and verify the local test bundle
```

The launcher uses an isolated `work/hd-test/user-data` profile and sets
`THREESX_HD_CG` to its sidecar directory. It leaves the user's installed archive
and profile intact. The bundle includes the locally-owned archive and must
remain local-only. Engine edits live in the separate, gitignored `3sx/` repo;
they are not tracked by the parent repository.

Start with [docs/hd-test-build.md](docs/hd-test-build.md) for the workflow, validation
and limitations, then [docs/character-atlas-trace.md](docs/character-atlas-trace.md)
for allocation/upload details. `docs/format-notes.md` retains historical format
investigations; its current-state section distinguishes those from the HD path.

## What this repo actually builds

1. `tools/afs_extract.py` reads the AFS index. `tools/decode_stages.py`,
   `tools/cg_decode_all.py` and `tools/decode_screens.py` decode local stage
   chunks, character frames and UI/menu screens into `work/decoded/`, with
   manifests. `tools/verify_screens.py` independently re-derives the screen
   source bytes. All 20 character archives present in the AFS are configured (pl00–pl14 and pl16–pl20; pl15 is absent).
2. `tools/export_for_upscale.py` assembles whole stage pages and stages character
   frames in `work/for_upscale/`. `tools/upscale.py` runs the existing local
   `realesr-animevideov3-x4` model through upscayl-bin. Both default to 4x; alpha
   is handled separately and stays binary. Outputs with an old scale regenerate.
3. `tools/cut_pages.py` writes stage chunks and copies character frames to
   `work/tiles/`; `tools/check_upscaled.py` validates dimensions and format.
4. Stages use `tools/ppg_repack.py --mode direct32`, then `tools/afs_rebuild.py`
   and `tools/verify_rebuild.py`. Rebuilding recalculates entry offsets and sizes.
5. HD character sidecars use `tools/cg_hd_pack.py` and `tools/cg_hd_verify.py`. The linear
   indexed `.hdcg` sidecars preserve palette indices and original AFS character data.
   `THREESX_HD_CG` points at a directory and the engine loads one `plNN.hdcg` per
   character group, so all 20 fighters are replaced in the same run.
   `tools/cg_repack.py` / `tools/cg_roundtrip_check.py` remain the separate 1x
   original-format AFS route; their restrictions are not an HD renderer ceiling.
6. `tools/build_hd_test.py` reuses existing stage repacks, regenerates/packs every
   character in `TEXGROUP`, verifies a rebuilt archive, builds/installs 3SX and writes
   a launcher plus bundle hashes. Regenerate stage repacks separately after editing
   stage art.

## Working agreement
- Prefer reading 3sx's own C source (`src/port/io/afs.c`,
  `src/sf33rd/Source/Common/PPGFile.c`, and `src/sf33rd/Source/Game/rendering/`) as the source of
  truth for format details, since it's already open and legally documents the container.
- All extraction/conversion tools should be pure, testable code — no hardcoded assumptions
  about a specific character/stage; validate against the AFS's own index table.
- When editing tools, run them against a **small sample slice** of the AFS first, not the
  whole file, to keep iteration fast and avoid accidentally dumping large asset sets into
  chat/logs.
- Logs and error messages should print offsets/sizes/format codes, not embed actual image
  data or base64 blobs.

## Next work

Review the test build across animations, costumes, both player slots and longer
matches for all 20 fighters. Vulkan and OpenGL smoke tests confirm HD uploads in
both player caches; they are not exhaustive visual or performance validation.
The pack quantizes to the original 64-color banks and retains stock pixels in
hidden tile regions. Shared tiles select their most-visible occurrence. All 20
sidecars are held in memory for the process lifetime: measured peak RSS is
2.63 GiB with the packs against 140 MiB for `--stock`. Per-group lazy loading is
the obvious reduction and is not implemented.

The next asset targets are everything that is neither a fighter nor a stage:
menus and their fonts, the round timer and health bars, the pre-match VS screen,
the game loading screen and the character select portraits.
[docs/ui-asset-survey.md](docs/ui-asset-survey.md) inventories those entries and
their formats. 14 of the 16 standalone `.ppg` screens decode and verify, and
`tools/build_screens.py` upscales and repacks them into `work/repacked/`, from
where `build_hd_test.py` bundles them with no engine change. `SelectNew.ppg` and
`Opening.ppg` need a ColorRAM palette source that is not in the file. The
`efNN.bin` HUD groups are not started.
