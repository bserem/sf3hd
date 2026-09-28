# Upscaling notes

## The upscale unit is a page, not a tile

The engine draws a stage as an 8-wide, 128px grid, chunk
`base + ((y >> 7) << 3) + (x >> 7)` per cell (`bg.c:623-625`), so every slotted
chunk has one fixed neighbour on each side. The first pipeline upscaled each
128x128 chunk on its own, which threw that away: the network had to invent all
four edges of every tile from nothing, and the four tiles meeting at a grid
corner were each invented independently.

Measured cost, over every page of every stage — mean |dRGB| across the 128px
tile boundaries divided by the same measure at a control column/row inside the
tiles (the `seam` metric from `verify_decoded.py`; 1.0 = a boundary is
indistinguishable from the interior):

| | per-tile 4x | per-page 4x, then cut |
|---|---|---|
| best page | 1.70 | 0.61 |
| median page | ~3.1 | ~0.99 |
| worst page | **6.04** (stage10 BG1) | **1.73** (stage18 BG0) |

So `export_for_upscale.py` assembles whole pages, `upscale.py` runs on those,
and `cut_pages.py` cuts them back onto the same grid into `work/tiles/`. Chunks
with no grid slot (the rewrite/extra substitution tiles of stages 3, 7, 8, 14,
18, 19 and stage07's three 256x256 Akane chunks) have no neighbours to recover
and are still upscaled one file at a time.

The cut is the exact inverse of the assembly, so at 1x it reproduces
`work/decoded/<stage>/chunk_NN.png` byte for byte — worth re-running whenever
the plan changes:

```sh
python3 tools/export_for_upscale.py
python3 tools/cut_pages.py --src work/for_upscale --out work/tiles_1x \
                           --verify-against work/decoded
```

The scale is never passed in; `cut_pages.py` reads it off each page image and
cross-checks it against the plan, so the 1x self-test and a 4x run are the same
code path.

## Scale is per folder: stages and characters 4x

Both now default to **4x**. `--scale N` still overrides a run. Existing outputs
are skipped only when every image has the requested dimensions and RGBA format;
old 2x folders are regenerated automatically.

Character HD frames are packed with `tools/cg_hd_pack.py` into an indexed sidecar,
loaded by the modified 3SX through `THREESX_HD_CG`. The old `cg_repack.py` AFS
route remains 1x. See [the test-build guide](hd-test-build.md).

## Model choice: `realesr-animevideov3-x4`

Run offline via `upscayl-bin` (ncnn/Vulkan), model files from
`~/Downloads/custom-models-main/models`.

The stage art is cel-style hand-painted background material, downsampled and
palettised for PS2. What we want back is smooth ink lines and flat, clean colour
fields, *not* invented photographic micro-texture. That rules out the
photo-restoration family.

Candidates compared on the three highest-detail chunks in the tree
(`stage09/chunk_34`, `stage12/chunk_43`, `stage03/chunk_45`):

| model | verdict |
|---|---|
| **realesr-animevideov3-x4** | **chosen.** Cleanest line reconstruction, flat areas stay flat, no invented grain. Also by far the fastest (XS network: ~0.3 s vs ~2.3 s for a 3-image batch on the 890M iGPU). |
| digital-art-4x (Upscayl bundled) | close second, similar cel look but softer/blurrier on fine lines and more contrast-punchy. |
| 4xHFA2k | good anime model, but adds visible texture in flat painted areas. |
| 4x_NMKD-Siax_200k, ultrasharp-4x, upscayl-standard-4x, remacri-4x | all photo-oriented: crunchy ringing and speckle noise in flat regions (very obvious on the stage09 water). |

`realesr-animevideov3` is Real-ESRGAN's anime-video model (upstream
`docs/anime_video_model.md`); it is the x4 weights, so `-z 4 -s 4` is a native
4x pass with no resampling afterwards.

## Alpha handling

`work/for_upscale/README.txt` requires alpha 0 to stay alpha 0 — 3sx's fragment
shader discards alpha-0 texels and samples with `GL_NEAREST`, so any bleed shows
up directly on screen. Source alpha is strictly binary: 668 of 1069 chunks are
`{0, 255}`, 398 are fully opaque, 3 are fully transparent. Nothing is partial.

`tools/upscale.py` therefore never lets the network touch the alpha channel:

1. **Colour bleed.** The decoder stores black under alpha-0 texels. Handing that
   to the upscaler drags a dark fringe several pixels *inside* every silhouette,
   which survives on the opaque texels. So the opaque colour is flood-expanded
   ~6 px outward (iterated premultiplied box blur, divided back out) and the
   network only ever sees an opaque image.
2. **Mask upscaled separately.** The binary mask is resized 4x bicubic and
   re-thresholded at 128, then reattached. Smoother silhouettes than
   nearest-neighbour, but the result is still strictly 0/255.
3. Fully transparent chunks skip the upscaler entirely.

Bleed and mask handling apply to pages exactly as they did to tiles — a page is
just a bigger RGBA image with the same strictly-binary alpha.

Verified: `tools/cut_pages.py` cuts all 21 stages cleanly at 4x and
`tools/check_upscaled.py` reports 0 problems over 1122 stage chunks (512x512
RGBA8, or 1024x1024 for stage07's three Akane chunks) and over all 24,760
character frames across the 20 groups, every one exactly 4x its manifest
bounding box and strictly binary alpha. `work/tiles/` is trimmed to the
character folders once stages are repacked, so a stage re-check needs its
chunks cut again first.

## Running it

```sh
# from the repo root
python3 tools/export_for_upscale.py   # work/decoded -> work/for_upscale (pages)
python3 tools/upscale.py              # everything, skips folders already done
python3 tools/upscale.py stage03 pl02
python3 tools/upscale.py --scale 2 stage03   # override the per-folder default
python3 tools/upscale.py --force      # redo everything
python3 tools/cut_pages.py            # work/upscaled -> work/tiles
python3 tools/check_upscaled.py       # gate work/tiles before repacking
```

`upscayl-bin` is located via `$UPSCAYL_BIN`, then `$PATH`, then the usual
install paths; the model directory via `$UPSCAYL_MODELS`.

Full run over all 21 stages: ~98 s on the 890M iGPU for 134 page/loose files
(down from 1069 separate tile passes), plus ~2 min to cut them back apart.
The 24,760 character frames take about 11 minutes for the full set.

`work/upscaled/<stage>/` may still hold per-tile `chunk_NN.png` from before
pages existed. Nothing reads them — `cut_pages.py` drives everything off the
manifest plan — but `tools/upscale.py --prune` deletes them once you are happy
with the new output.
