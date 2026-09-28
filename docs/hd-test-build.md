# Release v1 scope update

The builder now packages only the 21 stage/bonus backgrounds and 20 fighters.
It uses explicit allowlists, ignores other repacks, and rejects non-character
sidecars in its destination. The old `work/hd-test` experimental bundle is
preserved separately. Use `--reuse-bundle work/hd-test` to copy character packs
after checking their recorded hashes. Other research below remains historical.

# All-character HD local test build

From the repository root:

```sh
./work/release-v1/run.sh
```

All 20 character groups use 4x textures. The window defaults to 1536x896, a 4x canvas.
For a comparison with original character pixels and the same HD stages:

```sh
./work/release-v1/run.sh --stock
```

The launcher uses its own `work/release-v1/user-data` profile. It does not replace
`~/.local/share/CrowdedStreet/3SX/resources/SF33RD.AFS`. It includes all 21
previously repacked stage/bonus entries. The standalone verified archive is
`work/SF33RD_4x_test.AFS`. The bundle loads all 20 character sidecars from
`hd-cg/`; each archive group uses its matching pack, and `--stock` disables the
whole character directory at once. The bundle and all generated assets remain
under gitignored `work/` and are local-only.

## Rebuild

```sh
python3 tools/build_hd_test.py
```

Prerequisites are the local original AFS, extracted character frames, `work/repacked/*.ppg`,
the local upscaler/model and a RelWithDebInfo `3sx/build-test` build directory.
Configure it with `cmake -S 3sx -B 3sx/build-test -DCMAKE_BUILD_TYPE=RelWithDebInfo`
if needed; Release enables the original archive checksum gate.
The script regenerates outdated-scale character outputs, cuts/copies frames, validates
images, packs every character group in `TEXGROUP`, rebuilds/verifies the archive,
builds/installs 3SX and writes a launcher and SHA-256 bundle manifest. Existing
stage repacks are reused; regenerate them with
`tools/ppg_repack.py SF33RD.AFS --mode direct32` after changing stage art.

For just the character pipeline:

```sh
chars="pl00 pl01 pl02 pl03 pl04 pl05 pl06 pl07 pl08 pl09 pl10 pl11 pl12 pl13 pl14 pl16 pl17 pl18 pl19 pl20"
python3 tools/upscale.py $chars
python3 tools/cut_pages.py $chars
for cg in $chars; do python3 tools/cg_hd_pack.py SF33RD.AFS --cg "$cg.bin" --out "work/hd-cg/$cg.hdcg"; done
```

`--cg` is required on `cg_decode_all.py`, `cg_hd_pack.py` and `cg_hd_verify.py`;
its accepted values are the `TEXGROUP` keys in `tools/cg_decode_tile.py`.
`--limit-frames 3 --out work/hd-cg/sample.hdcg` on the packer makes a small
smoke-test update; other tile payloads then retain stock pixels. Omit the limit
for the complete pack. `cg_repack.py` remains the old 1x AFS route.

## Engine contract

`THREESX_HD_CG` names a directory of indexed sidecars, read once at startup.
The loader looks for `pl00.hdcg` through `pl20.hdcg` (there is no `pl15`); the
texgroup id is one higher than the archive number below 16, and equal at and
above it. With no directory, or for any group whose pack is missing or invalid,
the engine uses 1x atlases for that group. A valid 4x pack grows the two player
caches from nine 256x256 pages each to nine 1024x1024 pages each. Cache entry
counts, page selection, logical piece dimensions, geometry, flips and anchors
stay unchanged. CPU staging and FLTexture/GPU allocations grow together.
Background, UI and effect caches retain their existing dimensions.

All character transfer variants pass group and original tile identity to
`hdCgUpload`. HD pixels are only used when the destination cache is itself HD:
the same character groups are also uploaded into 1x caches (the opening's
`mlt_obj_melt2` uses cache 9), and those uploads stay stock. Replacements are
linear indexed pixels; RGB paths apply the current ColorRAM palette. Stock
transfers still unswizzle in the original 32x32 domain and expand into an HD
page when needed. The shared stock decode scratch does not hold HD tiles. UV
origins/spans use physical texels and physical atlas size; the scaled-object
inset is half a physical texel. Both backends upload from FLTexture dimensions,
so no separate hardcoded backend atlas sizes are needed.

The sidecar is little-endian:

- 24-byte header: magic `3SXCGHD1`, u32 scale (2/4), group, tile count, total bytes.
- One 12-byte directory record per original tile code: u32 original wh byte,
  absolute payload offset, payload byte count.
- Tightly packed linear indexed tiles, physical side = original side * scale,
  every index in [0,63]. The loader validates dimensions, offsets, lengths and
  indices before enabling HD. Missing/invalid packs fall back to stock.

The 20 packs total 219,943 tiles and 2,587,344,820 bytes (about 2.41 GiB) built
from 24,760 frames; 27.9% of sampled texels differ from stock. Each player's
indexed staging pages take 9 MiB at 4x, plus FLTexture backing and GPU storage.
All packs are read into memory at startup and stay resident for the process
lifetime. Measured peak RSS over a ~70 s attract/demo run: 2,760,844 KiB with
the sidecars against 143,172 KiB for `--stock`, so the packs cost about 2.5 GiB.
Loading a group's pack on first use would cut that to the two fighters on
screen; it is not implemented. Updates currently copy and recreate whole dirty
pages; subrectangle GPU uploads are a later optimization.

## Validation and limitations

Validated on the local checkout:

- All 24,760 character frames are 4x RGBA with binary alpha (`check_upscaled.py`,
  20 folders, 0 problems). Stage tiles were validated when their repacks were
  produced; `work/tiles/` currently holds only the character folders, so
  re-run `export_for_upscale.py`/`upscale.py`/`cut_pages.py` for a stage before
  re-repacking it.
- All 1,535 archive entries verified: 21 stage replacements and 1,514 unchanged.
- C atlas test under AddressSanitizer/UndefinedBehaviorSanitizer: 5,760 slot
  copies across 8/16/32 sides, 1x/2x/4x, indexed/RGB, HD/stock source, boundary
  slots and sentinels. The loader test covers valid/invalid payloads, palettes,
  the 1x-cache fallback and the no-pack path. Python tests cover stale-scale
  regeneration, palette indices and sidecar directory validation.
- Sample frames per character independently reconstruct the stock decode exactly
  in visible pixels; packed HD silhouettes match the full-frame HD targets in
  those samples. Local comparisons in `work/hd-cg/qa/` show stock, packed HD,
  and full-frame HD in that order.
- RelWithDebInfo build succeeds. Vulkan and OpenGL smoke tests (~70 s each,
  `work/runtime-hd-test.log` and `work/runtime-hd-test-opengl.log`) each load
  all 20 packs and log `HD CG: drawing group N in cache 3/4, 1024x1024 atlas`
  for both players, with no `rejected atlas copy` line. Force the OpenGL path
  with `SDL_GPU_DRIVER=invalid`.

This is an indexed test restoration, not lossless RGBA art: RGB is quantized
back to the original 64-color banks so alternate costumes still work. Shared
tiles select the occurrence with greatest visibility, with stable first-frame
ties. Hidden/uncropped regions retain nearest-scaled stock pixels. These choices
can differ from a full-frame upscale. Full animation, costume and prolonged
match visual review is still needed; the smoke tests do not establish that all
poses are artifact-free or benchmark gameplay performance.

Tests can be repeated with:

```sh
python3 -m unittest discover -s tests -p 'test_hd_pipeline.py'

cc -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined -I3sx/src \
   tests/hd_atlas_test.c -o /tmp/hd_atlas_test
/tmp/hd_atlas_test

# The loader test links hd_cg.c against the SDL3 built in the 3sx tree and
# writes its own one-tile fixture under $TMPDIR.
cc -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined \
   -I3sx/src -I3sx/third_party/sdl3/build/include \
   tests/hd_loader_test.c 3sx/src/sf33rd/Source/Game/rendering/hd_cg.c \
   -o /tmp/hd_loader_test -L3sx/third_party/sdl3/build/lib -lSDL3
rm -rf "${TMPDIR:-/tmp}/hd_loader_test"
LD_LIBRARY_PATH=3sx/third_party/sdl3/build/lib /tmp/hd_loader_test 1   # no pack
LD_LIBRARY_PATH=3sx/third_party/sdl3/build/lib /tmp/hd_loader_test 4   # with pack
```

## Bundle contents and logs

| Path under `work/release-v1/` | Purpose |
|---|---|
| `run.sh` | Isolated HD launcher; `--stock` disables character replacement |
| `application/bin/3sx` | Modified engine, with installed shaders/assets alongside |
| `application/lib/` | Installed SDL runtime libraries |
| `hd-cg/plNN.hdcg` | 20 indexed 4x character sidecars (pl00–pl14, pl16–pl20) |
| `user-data/CrowdedStreet/3SX/resources/SF33RD.AFS` | Archive with all 21 stage/bonus replacements |
| `user-data/CrowdedStreet/3SX/config` | Test-only preferences |
| `manifest.json` | SHA-256 hashes for executable, sidecars and archive |

The completed builder output is recorded in `work/bundle-hd-test.log`; runtime
smoke-test logs are `work/runtime-hd.log`, `work/runtime-hd-opengl.log` and
`work/runtime-all-cg.log`. Successful replacement reports
`HD CG: drawing group N ... 1024x1024 atlas` once per group. The separate
`HD CG: loaded ...` message confirms only pack loading, and
`HD CG: no valid pack for group N` names a group that stayed stock.
`HD CG: rejected atlas copy: ...` (logged once, with the page and tile
dimensions) means a replacement did not fit its destination page and the tile
fell back to stock. Restart after changing a pack because packs are loaded once
per process.

Engine source changes live in the separate `3sx/` repository, including
`hd_cg.c`, `hd_cg.h`, `hd_atlas.h`, `mtrans.c`, `texcash.c` and `PPGFile.c/.h`.
Preserve them along with the existing canvas, memory and alpha patches when
updating that checkout. The parent repository intentionally ignores `3sx/`.

## All-character extraction and upscale

The full local character set can be regenerated with:

```sh
for cg in pl00 pl01 pl02 pl03 pl04 pl05 pl06 pl07 pl08 pl09 pl10 pl11 pl12 pl13 pl14 pl16 pl17 pl18 pl19 pl20; do
  python3 tools/cg_decode_all.py SF33RD.AFS --cg "$cg.bin"
done
python3 tools/export_for_upscale.py
python3 tools/upscale.py
```

This produces 24,760 manifest-listed frames at 4x under `work/upscaled/plNN/`
(20 characters; pl15 does not exist in the source archive). The upscaler is the
local Upscayl `realesr-animevideov3-x4` model. Validate frame dimensions with
`tools/check_upscaled.py`, and use `tools/cg_hd_pack.py` and
`tools/cg_hd_verify.py` per character for pack-level checks.
