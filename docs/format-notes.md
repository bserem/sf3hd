> Update 2026-09-09: every character has an optional 2x/4x indexed sidecar
> loader. The 1x restrictions below describe the original AFS repack route. See
> [hd-test-build.md](hd-test-build.md) for the working test build and its limits.

# AFS container format notes

Source of truth: 3sx's own loader, `3sx/src/port/io/afs.c` (which credits
[AFSLib](https://github.com/MaikelChan/AFSLib) as inspiration). Line references below
are to that file as checked out in `3sx/`.

## STATE - resume here (2026-09-09)

### Done and verified

- All 21 stage/bonus repacks are 4x direct32 and included in the test archive.
- All 20 character groups (`pl00`-`pl14`, `pl16`-`pl20`; no `pl15`): 24,760
  exported frames at 4x, packed into 219,943 indexed HD tiles across 20
  sidecars, 2.41 GiB total. `tools/upscale.py` defaults stages and characters
  to 4x.
- The modified renderer loads a directory of sidecars from `THREESX_HD_CG`,
  scales player atlas storage and UV texel spans together, and preserves logical
  geometry and anchors. Stock and unrelated-group tiles fall back correctly in
  enlarged player pages, as do character tiles bound for a 1x cache.
- RelWithDebInfo build and Vulkan/OpenGL gameplay smoke tests passed; both
  backends logged HD uploads into the player caches' 1024x1024 atlases.
- Archive verification passed all 1,535 entries: 21 replacements and 1,514
  unchanged. Atlas safety tests checked 5,760 copies; loader and Python tests
  cover palette handling, invalid packs, fallback and outdated-scale outputs.
- Reconstructed sample silhouettes match the full-frame HD targets. Full
  animation/costume/long-match visual review and performance profiling remain.

### Test and rebuild

```sh
./work/hd-test/run.sh
./work/hd-test/run.sh --stock
python3 tools/build_hd_test.py
```

The launcher uses its own profile and archive under `work/hd-test/user-data`.
It does not replace the installed archive. The builder reuses the existing stage
repacks; regenerate those separately after changing stage art. See
[hd-test-build.md](hd-test-build.md) for commands, prerequisites and validation
limits.

### On disk

| Path | Contents |
|---|---|
| `work/decoded/` | Stage chunks and character frames with manifests/anchors |
| `work/for_upscale/` | Assembled stage pages and character frame inputs |
| `work/upscaled/` | 4x stage pages and character frames |
| `work/tiles/` | Cut 4x chunks; currently the 24,760 character frames |
| `work/repacked/` | 21 verified stage/bonus `.ppg` replacements |
| `work/hd-cg/plNN.hdcg` | 20 standalone 4x character sidecars |
| `work/hd-cg/qa/` | Stock / packed HD / full-frame HD sample comparisons |
| `work/hd-test/` | Installed engine, sidecars, isolated archive/profile, launcher and hashes |

`SF33RD.AFS` remains untouched. Everything under `work/` is gitignored and
local-only. The engine checkout is a separate repository with uncommitted
changes; preserving only the parent repository does not preserve engine edits.

### Engine settings and resolved blockers

| Setting | Current local default |
|---|---|
| `HD_MASTER_POOL` (`foundaps2.c`) | `0x20000000` (512 MiB) |
| `HD_TEXTURE_POOL` (`foundaps2.c`) | `0x18000000` (384 MiB) |
| `HD_PPG_DECBUF` (`main.c`) | `0x2000000` (32 MiB) |
| `CANVAS_SCALE` (both PC backends) | 4 |

The stage memory and GS-alpha fixes remain required. Stage10 needs 160 MiB of
texture backing. The decode buffer had to grow again for the UI screens:
`ppgSetupTexChunk_3rd` decompresses one whole chunk into it, and a 512x512
screen chunk at 4x in PSMCT32 is 16 MiB against a stage chunk's 4 MiB ceiling,
so 8 MiB aborted at load with "Failed to allocate texture data buffer".
`tools/build_screens.py` now checks the requirement against the engine's define
before repacking. At 4x, each player's nine indexed atlas pages require 9 MiB
of CPU staging plus FLTexture backing and GPU storage. Dirty pages are currently
copied/recreated in full. The 20 sidecars are read into memory at startup and
stay resident: measured peak RSS is 2.63 GiB against 140 MiB for a `--stock`
run. Loading each group's pack on first use is the obvious reduction and is
not implemented.

The original character `wh` format still cannot encode HD physical tile sizes.
The sidecar bypasses that restriction: its separate scale controls atlas storage
and sampling while `wh` continues to describe logical geometry. Cache page
selection and slot counts are unchanged. See
[character-atlas-trace.md](character-atlas-trace.md) for the implemented path.

`cg_repack.py` and `cg_roundtrip_check.py` still support the 1x AFS route. Their
original tests reproduced stock `pl02.bin` byte-for-byte, or all 725 Ryu frames
pixel-identically when forcing re-encoding. Use `cg_hd_pack.py` for HD; do not
increase the old packed dimensions or divide the fighter's game geometry.

### Remaining work

Review animation transitions, costumes and overlapping/shared tiles in longer
matches, across all 20 fighters and both player slots. Quantization retains
original palette banks; hidden regions keep stock pixels and shared tiles choose
the most-visible occurrence. The next asset targets are the non-character,
non-stage groups: menus, HUD, the VS and loading screens and the character
select portraits, inventoried in [ui-asset-survey.md](ui-asset-survey.md).

## Layout (confirmed against our local SF33RD.AFS)

| Offset | Size | Field | Reference |
|---|---|---|---|
| 0x00 | 4 | magic `"AFS\0"` (read as BE u32 `0x41465300`) | afs.c:11, 95-97 |
| 0x04 | 4 | `entry_count`, u32 LE | afs.c:104 |
| 0x08 | 8 * entry_count | entry table: u32 LE offset, u32 LE size | afs.c:110-121 |
| after table | 8 | attribute table pointer: u32 LE offset, u32 LE size | afs.c:128-131 |

Entries with `offset == 0` are empty/unused slots and are skipped for naming (afs.c:150).

### Attribute (name) table
48 bytes per entry (`AFS_ATTRIBUTE_ENTRY_SIZE`, afs.c:13). 3sx reads only the leading
NUL-terminated string, capped at 32 bytes (`AFS_MAX_NAME_LENGTH`, afs.c:14, 78-86).

The pointer record is looked for in two places, in this order (afs.c:128-145):
1. immediately after the entry table;
2. at `first_entry_offset - 8`.
Each candidate is validated by `is_valid_attribute_data()` (afs.c:53-76). Our file uses
case 1.

## Measured values for the local SF33RD.AFS
- file size: 642,492,416 (0x264BA800)
- entry_count: 1535
- first blob offset: 0x3800; last blob end: 0x264A85E0
- attribute table: offset 0x264A8800, size 0x11FD0 (= 1535 * 48 exactly), found via case 1

Names look correct and self-consistent (`Rank1.ppg`, `SE.bd`, `default.bin`,
`end000.bin`, ...), and blobs are 2048-byte aligned, which is consistent with a
PS2/CD sector-aligned container.

**Confirmed usable per entry:** index, byte offset, byte size, and a filename with a
meaningful extension. The extension is our only in-container type hint, and it is
enough to route entries to the right parser - e.g. all 57 `.ppg` entries parse cleanly
as PPG chunk streams (see below), which cross-validates the offsets and sizes the index
gave us: each entry's chunk chain terminates exactly on its last byte.

## Uncertainty / assumptions
- **Endianness**: header, entry table, and attribute pointer are read little-endian
  because afs.c uses `SDL_ReadU32LE` for all of them. Only the magic is big-endian
  (`SDL_ReadU32BE`), which is really just a byte-string compare. Confirmed sane: the
  parsed offsets are monotonically increasing and the final blob ends just under EOF.
- **Field widths**: offsets/sizes are u32. This caps an AFS at 4 GiB; our file is
  0.6 GiB, so nothing is observed near the limit and the width can't be disproven
  from this file alone. Taken on the loader's authority.
- **Attribute record bytes 32..47**: 3sx never reads them. AFSLib documents this tail
  as a timestamp (year/month/day/hour/minute/second as u16 LE) plus a duplicate u32
  file size. **We have not verified this** and the parser deliberately ignores those
  16 bytes. If we ever need dates or a size cross-check, verify first.
- **No per-entry format/type field exists in the container.** Texture format,
  dimensions, and palette are *not* in the AFS index; they live inside each blob
  (extension is the only hint: `.ppg`, `.bd`, `.bin`). Determining those is a separate
  step and will need `3sx/src/sf33rd/Source/Game/rendering/` (e.g. `texgroup.c`).
- Empty slots (`offset == 0`): none observed in the first 20 entries; not yet checked
  across all 1535.

## PPG texture container (`.ppg` entries)

Handled entirely inside 3sx's own decompiled code, **not** by external CRI middleware.
Sources:

- `3sx/src/structs.h:1289-1317` - `PPGFileHeader`, `PPXFileHeader`, `PPLFileHeader`
- `3sx/src/sf33rd/Source/Common/PPGFile.c:16` - `MAGIC_TO_INT`, magics compared as **big-endian** u32
- `PPGFile.c:957-996` (`ppgSetupTexChunk_1st`) - the chunk-walk loop
- `PPGFile.c:1169-1205` (`ppgSetupContextFromPPG`) - width/height/pixel-format decode
- `PPGFile.c:1098-1104` (`ppgSetupContextFromPPL`) + `PPGFile.c:33` (`pplColorModeWidth`) - palette decode
- `PPGFile.c:1071-1074` (`ppgSetupTexChunk_3rd`) - where pixel data starts inside a `pTEX`
- `PPGFile.c:408-438` (`ppgDecompress`) - compression codes
- `PPGFile.c:491-530`, `PPGFile.c:445-465` - `pPAL` / `pCMP` chunk walks (same shape)

### Container shape
A `.ppg` AFS entry is a flat **stream of chunks**, terminated by a `pEND` marker.
Every chunk begins with `u32 magic` (BE) + `u32 fileSize` (BE). The walk is
(`PPGFile.c:959-970`):

```
ofs = 0
loop:
    magic = BE32(at ofs)
    if magic == "pEND": stop
    if magic == "pTEX": this is a texture
    ofs += align_up(BE32(fileSize at ofs+4), 4)
```

`fileSize` **includes** the 16-byte chunk header. `pEND` is a bare 4-byte marker: the
loader compares `magic` and breaks before reading any other field (`PPGFile.c:960-962`),
so no `fileSize` follows it.

### Chunk magic table

All magics are 4 ASCII bytes compared as a big-endian u32 (`PPGFile.c:16`).

| Magic | Header struct | Size | Purpose | Reference |
|---|---|---|---|---|
| `pTEX` | `PPGFileHeader` | 16 B | texture bitmap | structs.h:1289-1298; PPGFile.c:963, 989 |
| `pPAL` | `PPLFileHeader` | 16 B | one or more palettes | structs.h:1309-1317; PPGFile.c:519 |
| `pCMP` | `PPXFileHeader` | 16 B | generic compressed blob | structs.h:1300-1307; PPGFile.c:458 |
| `pEND` | none | 4 B | end-of-stream marker | PPGFile.c:454, 515, 962 |

`pEND` carries no `fileSize`: the loader compares `magic` and breaks before reading
any other field (`PPGFile.c:960-962`).

### Codec table

`compress & 3` selects the codec, for both `pTEX` and `pPAL`
(`ppgDecompress`, `PPGFile.c:408-438`):

| Value | Codec | Implementation |
|---|---|---|
| 0 | raw / stored (straight copy) | `PPGFile.c:415-426` (`default` branch) |
| 1 | LZ77 | `decLZ77withSizeCheck`, `Source/Compress/Lz77/Lz77Dec.h` |
| 2 | zlib | `zlib_Decompress`, `Source/Compress/zlibApp.h` |
| 3 | raw / stored | falls through to the same `default` branch |

Every one of the 2611 `pTEX` chunks in our AFS uses codec 2 (zlib).

Chunk types seen in code: `pTEX` (texture), `pPAL` (palette, `PPLFileHeader`),
`pCMP` (generic compressed blob, `PPXFileHeader`), `pEND`.

### `pTEX` header - 16 bytes, big-endian (`structs.h:1289-1298`)

| Offset | Type | Field | Meaning |
|---|---|---|---|
| 0x00 | u32 BE | `magic` | `"pTEX"` |
| 0x04 | u32 BE | `fileSize` | chunk size incl. header; next chunk at `align4(fileSize)` |
| 0x08 | u8 | `width` | **width in 16-pixel units**; pixels = `width * 16` (`PPGFile.c:1171`) |
| 0x09 | u8 | `height` | height in 16-pixel units; pixels = `height * 16` (`PPGFile.c:1172`) |
| 0x0A | u8 | `compress` | `& 3` selects the codec |
| 0x0B | u8 | `pixel` | `& 3` pixel format, `& 4` endian flag, `& 0x20` desc variant |
| 0x0C | u16 BE | `formARGB` | channel layout for direct-colour modes |
| 0x0E | u16 BE | `transNums` | count of entries in the trans table that follows the header |

Pixel payload starts at `transNums * 3 + 0x10` from the chunk start
(`PPGFile.c:1072-1074`), i.e. a `transNums`-element, 3-bytes-each table sits directly
after the header. Compressed payload length = `fileSize - (transNums * 3 + 0x10)`
(`PPGFile.c:1074`).

`pixel & 3` (`PPGFile.c:1174-1195`):

| Value | Format | bitdepth | pitch |
|---|---|---|---|
| 0 | 4bpp indexed | 0 | `width / 2` |
| 1 | 8bpp indexed | 1 | `width` |
| 2 | 16bpp direct | 2 | `width * 2` |
| 3 | 32bpp direct | 4 | `width * 4` |

Decompressed bitmap size = `pitch * height` (`PPGFile.c:1075`).

`pixel & 4` is passed as `dendL` to `ppgChangeDataEndian` (`PPGFile.c:1080`), i.e. a
"data already byte-swapped" flag, not a pixel-format bit.

`formARGB` (`PPGFile.c:1197+`, compared after `SDL_Swap16BE`): `0x1555`, `0x0565`,
`0x4444`, anything else falls through to `0x8888` (ARGB32). Indexed textures carry
`formARGB == 0` and take their colours from a `pPAL` chunk instead.

`compress & 3` (`PPGFile.c:414-437`): `0` = stored/raw copy, `1` = LZ77
(`decLZ77withSizeCheck`, `Source/Compress/Lz77/`), `2` = **zlib** (`zlib_Decompress`),
`3` = falls into the `default` raw branch.

### `pPAL` header - 16 bytes, big-endian (`structs.h:1309-1317`)

| Offset | Type | Field | Meaning |
|---|---|---|---|
| 0x00 | u32 BE | `magic` | `"pPAL"` |
| 0x04 | u32 BE | `fileSize` | chunk size incl. header |
| 0x08 | u16 | `free` | unused |
| 0x0A | u8 | `compress` | `& 3`, same codec table as `pTEX` |
| 0x0B | u8 | `c_mode` | `& 3` colour count, `& 4` endian flag |
| 0x0C | u16 BE | `formARGB` | colour layout; `0x8888` => 4 bytes/colour, else 2 |
| 0x0E | u16 BE | `palettes` | number of palettes packed in this chunk |

Colours per palette = `pplColorModeWidth[c_mode & 3] + 1` = **16 / 64 / 256 / 1**
(`PPGFile.c:33`, `PPGFile.c:537`). Payload starts immediately after the 16-byte header
(`cmpAdrs = ppl + 1`, `PPGFile.c:534`) and expands to
`bytes_per_colour * palettes * colours_per_palette` (`PPGFile.c:550`). Palettes are
stored back to back, stride `colours_per_palette * bytes_per_colour`
(`PPGFile.c:578`).

### Verified against the local SF33RD.AFS
`tools/ppg_inspect.py` walked **all 57 `.ppg` entries** header-only: 2611 `pTEX`,
12 `pPAL`, 57 `pEND`, **zero** unknown magics, bad sizes, or overruns, and in every
entry the chunk chain ends exactly on the AFS entry's last byte (0 bytes slack).

Observed distribution:
- pixel modes: 1412 x 16bpp direct, 1193 x 8bpp indexed, 6 x 4bpp indexed
- dimensions: 2499 x 128x128, 99 x 256x256, 5 x 512x512, 4 x 512x256, 3 x 128x256, 1 x 256x128
- compression: **zlib on all 2611** textures
- `formARGB`: 0x1555 on all direct-colour textures, 0x0000 on all indexed ones

## Uncertainty / assumptions - PPG
- **Endianness is mixed and worth re-checking per field.** Magics, `fileSize`,
  `formARGB` and `transNums` are read through `SDL_Swap32BE` / `SDL_Swap16BE`, so they
  are big-endian on disk (PS2 is little-endian, so the data was authored BE). The
  single-byte fields have no endianness. This is taken from the loader, and every
  parsed value is self-consistent across 2611 chunks, so confidence is high.
- **`transNums`**: RESOLVED, see "The trans table" below. (An earlier revision of
  this document wrongly asserted it was always 0 across our AFS; that was an
  unmeasured overgeneralisation from two files.)
- **`pixel & 0x20`** sets renderer desc `0x24` instead of `0x14` for 4bpp textures
  (`PPGFile.c:1176-1181`). What that desc bit means is not decoded here; likely a
  swizzle/CLUT-layout selector on the PS2 side. Not needed for header parsing, but it
  **will** matter before decoding 4bpp pixels.
- **Swizzling: RESOLVED - `pTEX` payloads are linear.** See the section below.
- **`pPAL` 64-colour case looks inconsistent in the loader**: `PPGFile.c:1100` sets the
  renderer context width to 16 or 256 only, so `c_mode & 3 == 1` (64 colours) gets
  width 256, while the per-palette stride at `PPGFile.c:578` uses 64. We follow the
  stride (`col_items`) for data layout. Not exercised by our file yet - none of the
  12 `pPAL` chunks has been checked for `c_mode`.
- **`pCMP` / `PPXFileHeader`** (`structs.h:1300-1307`, `PPGFile.c:445-465`) is
  recognised by the walker but not decoded; no `pCMP` chunk appears in any `.ppg`
  entry in our file.
- Only **57 of 1535** AFS entries are `.ppg`. The bulk of the game's art is in other
  extensions (`.bin` and friends) whose formats we have not looked at yet, so `.ppg`
  is *a* texture container, not necessarily *the* main one.

## Swizzle / tiling question - resolved

**Conclusion: `pTEX` pixel payloads are stored linearly, `pitch` bytes per row, top row
first. No de-swizzle step is needed.**

### Why the conversion table is a red herring

`ppgMakeConvTableTexDC()` (`PPGFile.c:864-887`) fills `dctex_linear`, a 1024-entry
`s16` table (backing store `dctex_linear_mem[0x800]`, `main.c:43`, wired up at
`main.c:113`, built once at `main.c:127`). It is built from two seed arrays:

```
seed[32]    = 0x0000,0x0002,0x0008,0x000A,0x0020,...,0x02AA   (bits 1,3,5,7,9)
seedAdd[16] = 0x0000,0x0004,0x0010,0x0014,0x0040,...,0x0154   (bits 2,4,6,8)
dctex_linear[j + i*64]      = seed[j] + seedAdd[i]
dctex_linear[j + i*64 + 32] = dctex_linear[j + i*64] + 1       (bit 0)
```

Decoding the index arithmetic: the table is addressed as `dctex_linear[x + y*32]`
(see the consumers, e.g. `PPGFile.c:780` `dctex_linear[j + (i << 5)]`), so index bits
0-4 are `x` and bits 5-9 are `y`. Reading off which value bit each input bit lands in:

| input | goes to value bit |
|---|---|
| x0 x1 x2 x3 x4 | 1, 3, 5, 7, 9 |
| y0 y1 y2 y3 y4 | 0, 2, 4, 6, 8 |

That is a plain **Morton / Z-order interleave over a 32x32 tile**, with `y` in the even
bit positions and `x` in the odd ones. So the table *is* a twiddle table - just not one
that applies to files on disk.

**It is never used on PPG file data.** `dctex_linear` has exactly one consumer,
`ppgRenewDotDataSeqs` (`PPGFile.c:749-858`, uses at lines 780, 794, 805, 823, 837,
848), and that function has exactly one caller: `njReLoadTexturePartNumG`
(`dc_ghost.c:282-285`). The `nj*` prefix is Sega's Ninja SDK, and the function name is
`...TexDC` - this is the **Dreamcast** twiddled-texture path in a compatibility shim
for the ghost/afterimage effect, updating sub-rectangles of an already-resident
texture. It is not part of loading a `.ppg`.

The actual file-load path, `ppgSetupTexChunk_3rd` (`PPGFile.c:1064-1090`), does:
decompress -> `ppgChangeDataEndian` -> `flCreateTextureHandle(&bits, attribute)` with
`bits.pitch` straight from `ppgSetupContextFromPPG`. No table lookup, no reordering.

### Endianness of pixel data

`ppgChangeDataEndian(buf, size, ppg->pixel & 4, ppg->formARGB == 0x8888, bitdepth)`
(`PPGFile.c:1080`, body at `PPGFile.c:654-673`):

- `bitdepth` 0 or 1 (4bpp / 8bpp indexed): returns immediately, no swap.
- `pixel & 4` set: returns immediately - data is already in native order.
- otherwise: byteswap every `u16` (or every `u32` when `formARGB == 0x8888`).

So 16bpp pixels are stored **big-endian** on disk unless `pixel & 4` is set.

### Empirical confirmation

`tools/ppg_decode_test.py` on entry 0 (`Rank1.ppg`), `pTEX` #0: payload inflates to
exactly 32768 bytes = `pitch(256) * height(128)`, matching the header prediction with
no slack. Decoding it linearly as big-endian ARGB1555 produces a clean image with no
block scrambling, no diagonal shear, and correct orientation.

Decoding all 8 `pTEX` chunks of `Rank1.ppg` and laying them out 4-across x 2-down
yields a **seamless 512x256 teal diagonal gradient** - the gradient runs continuously
across every tile boundary. Contiguous gradients across tile seams are a strong check:
any wrong tile-internal ordering would show as visible blocking, and any wrong row
order would break the seams.

### Remaining uncertainty here
- **The 4x2 arrangement was inferred visually, not read from the file.** Nothing in the
  PPG container records how chunks compose into a larger image. Do not assume row-major
  NxM for other entries - it happens to be right for `Rank1.ppg`. For stage backgrounds
  the real placement *is* recoverable, but from game tables rather than the container;
  see "Tile placement for stage backgrounds" below.
- Only the **16bpp / `formARGB` 0x1555** path has been exercised end to end. The 8bpp
  and 4bpp indexed paths (1193 and 6 chunks respectively) need a `pPAL` chunk and have
  **not** been decoded or validated yet. 4bpp in particular still has the undecoded
  `pixel & 0x20` desc bit (`PPGFile.c:1176-1181`).
- `formARGB` 0x0565, 0x4444 and 0x8888 do not occur in our file, so their handling is
  transcribed from the loader but unverified.

## Open questions

1. ~~**`transNums` semantics - unverified.**~~ **RESOLVED** - it is the
   per-sub-rectangle *palette* map, not a transparency list. See "The trans table"
   below. **Correction:** an earlier revision of this document claimed all 2611
   textures have `transNums == 0`. That was wrong and was never actually measured -
   only `Rank1.ppg` and `Ending19.ppg` had been checked, and both happen to be 0.
   `stage00.ppg` has `transNums` between 1 and 16 on *every* chunk.
2. **4bpp decoding is untested.** 16bpp direct and 8bpp indexed are both validated
   end to end; the 6 4bpp chunks are not. See "Still open after this step" below.
3. **Palette source for 8bpp textures in entries with no `pPAL` chunk** is unknown -
   most of the 1193 8bpp chunks are in such entries. See "Still open after this step".

## Palette binding for indexed textures - resolved

**The palette lives in a `pPAL` chunk inside the same `.ppg` chunk stream. The
container does NOT record which palette belongs to which texture - the pairing is
hardcoded in game code.**

### Same file, not a separate one

Every caller sets up the palette and the textures from the *same* buffer. The
`loadAdrs`/`loadSize` pair passed to `ppgSetupPalChunk` is byte-for-byte the one passed
to `ppgSetupTexChunk_1st` on the next line:

- `bg.c:331-332`, `bg.c:348-349`, `bg.c:503-504`, `bg.c:521-522`
- `demo00.c:213-214`, `demo00.c:289-291`
- `sc_sub.c:219-223` (explicitly commented `// scrscrn.ppg` at `sc_sub.c:213`)

So one `.ppg` entry is self-contained: `pTEX` chunks and their `pPAL` chunk(s) coexist
in one chunk stream.

### How a texture finds its palette - it doesn't

There is **no palette id, index, or back-reference field in `PPGFileHeader`**
(`structs.h:1289-1298`). The binding is made entirely by the caller, on three levels:

1. **Which `pPAL` chunk** - the `num` argument to
   `ppgSetupPalChunk(pch, adrs, size, ixNum1st, num, unused)`. The walk loop skips
   `num` chunks whose magic is `pPAL` before stopping (`PPGFile.c:511-530`). So `num`
   is an *ordinal over `pPAL` chunks in stream order*, chosen by game code.
   `sc_sub.c:219-222` loads `num` = 3, 2, 1, 0 from one file into four different
   `Palette` objects.
2. **Which `Palette` object pairs with which `Texture`** - the `PPGDataList { tex, pal }`
   struct (`structs.h:1284-1287`), selected by `ppgSetupCurrentDataList`
   (`PPGFile.c:104-106`). These lists are fixed globals (`PPGWork.c:8-45`).
3. **Which sub-palette inside the chunk** - `ppgSetupCurrentPaletteNumber(pal, num)`
   (`PPGFile.c:108-118`) picks `pal->handle[num]`, i.e. one of the `palettes` packed
   palettes. Called at draw time, e.g. `njSetPaletteBankNumG` (`dc_ghost.c:274-276`).

**Practical consequence for this pipeline:** for an entry with exactly one `pPAL`
holding exactly one palette, the pairing is unambiguous and recoverable from the file
alone. For entries with several, we cannot recover the intended pairing from the
container - it must be read out of the game source, or guessed and checked visually.

Survey of the 7 `.ppg` entries in our AFS that contain a `pPAL`:

| idx | name | pTEX | pPAL chunks | unambiguous? |
|---|---|---|---|---|
| 8 | Ake3rd.ppg | 3 x 8bpp | 1 chunk, 3 x 64 colors | no - 3 sub-palettes |
| 10 | scrscrn.ppg | 5 x 4bpp, 2 x 8bpp | 4 chunks (32x16, 260x16, 1x256, 1x256) | no |
| 12 | Warning.ppg | 2 x 8bpp | 2 chunks, 1 x 256 each | no - 2 chunks |
| 13 | Warninge.ppg | 2 x 8bpp, 2 x 16bpp | 2 chunks, 1 x 256 each | no - 2 chunks |
| 73 | Ending19.ppg | 1 x 8bpp, 32 x 16bpp | 1 chunk, 1 x 256 | **yes** |
| 75 | CapLogo.ppg | 1 x 4bpp | 1 chunk, 32 x 16 colors | no - 32 sub-palettes |
| 1408 | stage07.ppg | 52 x 8bpp | 1 chunk, 3 x 256 | no - 3 sub-palettes |

Note that most of the 1193 8bpp `pTEX` chunks are in entries with **no `pPAL` at all**,
so their palettes must come from elsewhere. Not yet investigated.

### `pPAL` payload layout

Payload starts immediately after the 16-byte header (`cmpAdrs = ppl + 1`,
`PPGFile.c:534`), decompresses to
`entry_bytes * palettes * colors_per_palette` (`PPGFile.c:550`), and the packed
palettes are contiguous with stride `colors_per_palette * entry_bytes`
(`PPGFile.c:578`). Palette colour data is byteswapped by the same
`ppgChangeDataEndian` call (`PPGFile.c:566`) gated on `c_mode & 4`, so 16-bit palette
entries are **big-endian on disk**, exactly like 16bpp texture pixels.

## Swizzle by bit depth - checked, not assumed

Index data at 4bpp and 8bpp is **linear, the same as 16bpp**. Verified in the renderer,
not inferred:

- `flPS2ConvertTextureFromContext` (`flps2vram.c:950-1050`) dispatches on the GS pixel
  format. `SCE_GS_PSMT4` (`flps2vram.c:975-979`) and `SCE_GS_PSMT8`
  (`flps2vram.c:981-985`) are handled by a **straight `flMemcpy`** - no reordering, no
  per-tile traversal. Only the direct-colour formats (`PSMCT16/24/32`) go through
  `flPS2ConvertContext`, and that is a channel-layout conversion, not a swizzle.
- **No CLUT entry reordering either.** `flCreatePaletteHandle` (`flps2vram.c:161-167`)
  passes `type = 1` to `flPS2ConvertContext` when the palette is 256 wide. But `type`
  only selects a different *addressing* mode (`flps2vram.c:1078-1085`, `1106-1112`):
  it indexes by a running counter `wk0` instead of `pitch * y + bitdepth * x`. For a
  palette, `height == 1` (`PPGFile.c:1101`), so both expressions are identical. It
  ignores pitch; it does not permute entries.

**Empirically confirmed.** `tools/ppg_decode_indexed.py` has a `--clut-unswizzle`
diagnostic that applies the usual PS2 GS 256-entry CLUT permutation (within each block
of 32, entries 8-15 swap with 16-23). On `Ending19.ppg`:

- **without** the permutation: a clean, smoothly shaded image
- **with** it: heavy posterised banding and black blotches

so the on-disk palette is in plain index order and must be used as-is.

### Verified end to end
`Ending19.ppg` (AFS idx 73), the one unambiguous case: 512x512 8bpp `pTEX`, indices
inflate to exactly 262144 bytes = `pitch(512) * height(512)`, 82 distinct indices with
max index 82 against a 256-entry palette (no overflow). The result is a coherent
underwater scene - sunlight rippling through the water surface at the top, dark seabed
silhouettes below - with correct orientation and no block scrambling.

### Still open after this step
- Only **8bpp** is validated. The 6 4bpp chunks are untouched, and 4bpp still carries
  the undecoded `pixel & 0x20` desc bit (`PPGFile.c:1176-1181`) plus the question of
  whether the two 4-bit nibbles are low-first or high-first, which nothing above
  settles.
- Where the palettes come from for the many 8bpp textures in entries with **no** `pPAL`
  chunk is unknown. `color3rd.c:497-509` uses `ppgSetupPalChunkDir` with a
  caller-supplied `PPLFileHeader`, which suggests some palettes arrive by a different
  route entirely - worth following before batch decoding.

## Palette resolution for `pPAL`-less entries - resolved

The 1131 8bpp `pTEX` chunks that live in entries with no `pPAL` are almost all stage
backgrounds. Their palettes come from a **separate AFS entry**, and the pairing is
fully recoverable offline from two tables compiled into the game.

### The palette is not generated, and not inside the .ppg

`ppgSetupPalChunkDir` (`PPGFile.c:592-651`) takes a caller-supplied `PPLFileHeader`
plus a raw data pointer, instead of parsing a chunk stream. Its only callers,
`color3rd.c:497` and `color3rd.c:509`, hand it a **synthesised** header and a
**zero-filled** buffer (`color3rd.c:479-510`): these register two empty palette banks,
`palDC` and `palCP3`, to be filled at runtime. So `ppgSetupPalChunkDir` itself is not
a file-loading route - it just reserves handles.

The stage BG lists point at that runtime bank: `ppgBgList[i].pal = palGetChunkGhostCP3()`
(`bg.c:60-61`, `bg.c:64-65`, returning `&col3rd_w.palCP3`, `color3rd.c:517-519`).

The actual colours are `ColorRAM[512][64]` (`color3rd.h:8`, defined `color3rd.c:55`) -
CPS3 palette RAM, 512 banks of 64 colours. `palUpdateGhostCP3(pal, nums)`
(`color3rd.c:540-553`) copies `0x80` bytes (= 64 x u16) from `ColorRAM[i]` into palette
handle `i`.

### Which file fills ColorRAM - two compiled-in tables

`load_any_color(ix, kokey)` (`color3rd.c:174-184`) looks up `color_file[ix]`
(`col_file_data { u16 data, type, apfn, free }`, `color3rd.c:27-32`; table at
`color3rd.c:566`, 161 entries). **`apfn` is an AFS file number.** It loads that entry
and calls `init_trans_color_ram(0, key, cfn->type, cfn->data)`.

For backgrounds, `type == 2` (`color3rd.c:262-278`): the file is read as a flat `u16`
array into `ColorRAM[data]` onward, where `data == 0x012C` (300). No byteswap is
applied, so these files are **little-endian**.

The `.ppg` <-> palette-file pairing comes from the load-request table
`ldreq_tbl[]` (`gd3rd_data.c`), where a `LDREQ_COLOR` slot and a `LDREQ_SCREEN` slot
sit in the same 5-slot group and both index `color_file[]`
(`gd3rd.c:32-33` routes both to `q_ldreq_color_data`). Pairing every such group gives a
complete, unambiguous map:

| slot | COLOR -> file | SCREEN -> file |
|---|---|---|
| 100 | bg000.bin | stage00.ppg |
| 105 | bg010.bin | stage01.ppg |
| 110 | bg020.bin | stage02.ppg |
| ... | ... | ... |
| 160 | bg120.bin | stage12.ppg |
| 165 | bg130.bin | stage13.ppg |
| ... | ... | ... |
| 195 | bg190.bin | stage19.ppg |
| 200 | bns00.bin | Bonus00.ppg |
| 206 | bns10.bin | Bonus01.ppg |

The rule reduces to **`stageNN.ppg` pairs with `bgNN0.bin`** for all 20 stages plus the
two bonus stages. Note this is *not* recoverable from AFS adjacency: in the index
`bgNN0.bin` sits immediately after `stage(NN-1).ppg`, which would pair everything off
by one. It also is not recoverable from `color_file[]` ordering alone, because
`bg120.bin` was appended out of order at row 88. The `ldreq_tbl` grouping is the only
reliable source. (Slot 185 re-pairs stage11/bg110 - consistent with there being no
`stage17.ppg` in the AFS.)

### Palette colour order: ABGR1555, not ARGB1555

`palConvSrcToRam` (`color3rd.c:426-441`) swaps red and blue: `palFormSrc` has red at
bit 10 and `palFormRam` has red at bit 0 / blue at bit 10 (`color3rd.c:450-478`).

**Empirically, the on-disk `bgNN0.bin` values must be read as ABGR1555** - red in the
low 5 bits, blue in bits 10-14 - to produce correct colour. Rendering `stage00.ppg`
four ways (LE/BE x ARGB/ABGR) gives one obviously right answer: LE + ABGR yields a blue
sky with white clouds; LE + ARGB gives a sepia/orange sky; both BE variants are garish
noise. This is the opposite channel order from `pPAL` chunks, which are ARGB1555 and
big-endian - and the existence of the `palConvSrcToRam` swap is exactly what reconciles
the two. Treat the decomp's `palFormSrc` field *names* as nominal; the measured byte
order is what matters.

## The trans table - per-sub-rectangle palettes

**This resolves the last open question and is what makes stage backgrounds decodable.**

The `transNums` 3-byte records that sit between the `pTEX` header and the pixel payload
are a **palette map**, consumed by `ppgWriteQuadUseTrans` (`PPGFile.c:239-370`). The
key line is `PPGFile.c:322`:

```c
palhan = phan[*tran + pal];     // pal = caller's base palette code
```

then `PPGFile.c:324-331`:

```c
tran++;  iPoint = *tran++;  cofsXY = *tran++;
xs = (cofsXY >> 4) + 1;   ys = (cofsXY & 0xF) + 1;
sx = iPoint % ppgw;       sy = iPoint / ppgw;   // ppgw = ppg->width, i.e. 16px units
```

So each record is:

| byte | field | meaning |
|---|---|---|
| 0 | palette offset | added to the caller's base palette code |
| 1 | `iPoint` | position: `sx = iPoint % width_units`, `sy = iPoint / width_units` |
| 2 | `cofsXY` | size: `xs = (hi nibble) + 1`, `ys = (lo nibble) + 1` |

**All values are in 16-pixel units** (`width_units` is the raw `width` header byte).
A texture is drawn as `transNums` separate quads, each covering
`(sx*16, sy*16)` to `((sx+xs)*16, (sy+ys)*16)`, each with its own palette bank.

This is the CPS3 per-tile palette mechanism preserved through the PS2 port, and it means
**the palette assignment is stored in the file** - no game-state emulation required.

When `transNums == 0` the code falls through to `PPGFile.c:373-379`, which uses a
single palette chosen by the caller's `cix` argument - that is the genuinely
context-dependent case.

The caller's base for stage backgrounds is `bgPalCodeOffset[bgnm]`, set uniformly to
`0x12C` (300) at `bg.c:258-260` and passed through `bgDrawOneChip`
(`bg.c:1084-1095`) into `ppgWriteQuadUseTrans`. Since `bgNN0.bin` is loaded at
`ColorRAM[300]`, base 300 cancels out: **the trans record's palette offset byte is
directly the 64-colour bank index within `bgNN0.bin`.**

(Other contexts override the base - `bgPalCodeOffset[0] = 144` in `sel_pl.c:1620`,
`next_cpu.c:300/355/1017`, `menu.c:1445`, and `bg.c:419` sets
`etcBgPalCnvTable[type] + 144`. Those are select/menu screens, not in-match stages.)

### Verified end to end
`stage00.ppg` (AFS idx 1387) + `bg000.bin` (AFS idx 1383, 8704 bytes = 68 banks of 64
colours). All 46 `pTEX` chunks decoded with per-sub-rectangle palettes and assembled
8-across produce a **fully coherent temple interior**: domed ceiling with relief
detail, a receding colonnade, a patterned mosaic floor, and blue sky with white cloud
above. Smooth gradients, correct orientation, no scrambling, and none of the stray
bright-green pixels that appear under a single-bank decode.

That green is itself the diagnostic: bank 0 of `bg000.bin` has pure `(0, 247, 0)` at
indices 54 and 63 - unused-slot filler. A single-bank decode of a city tile lights those
up all over; with the trans table applied they vanish, because those indices belong to
sub-rectangles that use other banks (chunk 40 alone references banks
1, 2, 4, 5, 8, 10, 12, 14, 15, 16, 52, 53, 54, 55).

### What is still context-dependent
- Entries whose `pTEX` chunks have `transNums == 0` **and** no `pPAL` fall back to the
  caller's `cix`, which is game state. `Ending19.ppg` and `Ake3rd.ppg` have
  `transNums == 0` throughout; `Ending19` is saved by having its own `pPAL`.
- The base palette code is per-context (300 for in-match stages, 144 for select/menu).
  For stage decoding it is constant, but a general tool cannot assume it.
- Character sprites are not covered here at all: `plNNpl.bin` / `plNNplm.bin` /
  `PLNN.bd` use `color_file` types 1, 3 and 10, which run different handlers
  (`color3rd.c:206-260` and the `cse*` bank path), and player colour choice
  (`Player_Color[]`) selects among alternates at runtime. Palette selection for
  characters **is** genuinely per-context.

## Tile placement for stage backgrounds

A `.ppg` packs its `pTEX` chunks consecutively, but the game assigns them to **sparse
grid slots**, so *chunk N is not grid slot N*. Dumping chunks in file order produces a
correctly-coloured but visually shuffled image.

The mechanism (`bg.c:299-312`):

```c
tgbix = bgtex_stage_gbix[bg_w.stage][j];      // 32-bit presence mask, bg_data.c:378
mask  = 0x80000000;                            // bit 31 == slot 0
ppgSetupTexChunk_1st(NULL, loadAdrs, loadSize, (stg * 64) + 0x84, 32, 0, 0);
ppgSetupTexChunk_1st_Accnum(0, accnum);        // carry accnum across layers
for (i = 0; i < 32; i++, mask >>= 1) {
    if (tgbix & mask) {
        accnum = ppgSetupTexChunk_2nd(NULL, i + ((stg * 64) + 0x84));
        ...
    }
}
```

`ppgSetupTexChunk_2nd` (`PPGFile.c`) does `hnof->b16[1] = tch->accnum++`, i.e. **each
set bit consumes the next chunk in file order**. `accnum` carries across layers, so all
layers draw from one sequential stream.

Screen position comes from `bg.c:623-625`: `global_index_real = global_index +
((y >> 7) << 3) + (x >> 7)`, i.e. an **8-slot-wide grid at 128px steps**, so
slot -> `(slot % 8, slot / 8) * 128`.

### Confirmed on stage00
`bgtex_stage_gbix[0]` = `{ 0xF0F0F0F0, 0x7F7FFFFF, 0x0 }` -> popcounts 16 + 30 + 0 =
**46**, exactly the `pTEX` count of `stage00.ppg`. Decoding with per-layer masks:

- **layer 0** (16 tiles, slots 0-3/8-11/16-19/24-27, a 4x4 block = 512x512): sky
  backdrop, clouds, distant temple on a hill.
- **layer 1** (30 tiles, all slots but 0 and 8): the temple interior - continuous
  columns, unbroken mosaic floor, correct perspective.

Both are coherent once placed. Renders: `work/upscaled_test/09_stage00_layer*_placed.png`.

### Caveats
- Layers must be separated before placement; concatenating them into one sheet splits a
  layer boundary mid-row, which is what makes a naive file-order dump look scrambled.
- This covers *static* slot placement only. Actual on-screen composition also involves
  per-layer scroll/parallax, priority (`bg_priority`, `stage_priority`), and the
  "rewrite" tile substitution path (`rw_dat`, `rw_gbix`, `bg.c:626-648`) that swaps
  certain slots at runtime. None of that is needed to extract correct tiles, but a
  faithful full-scene mock-up would need it.
- `bgtex_stage_gbix` is `[22][3]` - 22 stages x 3 layers, but only the first
  `use_real_scr[stage]` rows are used. See "Batch stage decode" below: all 21 stages
  are now verified against their chunk counts.

## Batch stage decode - full chunk accounting

Running the stage00-validated pipeline over every stage (`tools/decode_stages.py`)
exposed two more consumers of the sequential `accnum` stream. With them, **all 21
stage `.ppg` files account for 100% of their `pTEX` chunks exactly** - no slack, no
guesses.

The full budget for a stage is:

```
chunks = sum(popcount(bgtex_stage_gbix[stage][j]) for j < use_real_scr[stage])
       + rewrite_scr[stage]
       + (3 if stage == 7)
```

- `use_real_scr[22]` (`bg_data.c:41`) = `{2,1,3,2,1,2,1,2,2,2,2,1,2,2,3,2,2,2,2,1,2,1}`
  bounds the layer loop at `bg.c:301`. **`bgtex_stage_gbix` rows contain masks past
  this bound that are never consumed** - e.g. stage07's third mask (18 bits) is dead.
  Using all three rows unconditionally over-counts.
- `rewrite_scr[22]` (`bg_data.c:45`) =
  `{0,0,0,25,0,0,0,12,24,0,96,0,0,0,1,0,0,0,10,18,0,0}` - extra chunks pulled from the
  *same* `accnum` stream at `bg.c:316-326`, into `ppgRwBgList`. For most stages these
  are runtime substitution tiles (animated water, crowd, signage) with **no fixed grid
  slot**, so they are only meaningful as individual chunk images. **Stage 10 is the
  exception - see below.**

### Stage 10 (Yang / Hong Kong): the 96 "rewrite" chunks are whole layer pages

Stage 10 has 160 `pTEX` chunks, 2-3x every other stage, and 96 of them look like
noise in isolation. They are not garbage and the budget is not wrong: they are
**three more full pages of BG1**.

`Bg_Kakikae_Set` sets `tokusyu_stage = 2` for stage 10 (`bg.c:100-106`) instead of
wiring up an `rw_dat` / `rw_gbix` substitution script. The `tokusyu_stage == 2` arm of
the draw loop (`bg.c:733-777`) does not swap individual slots; it offsets the whole
layer:

```c
global_index_real = global_index + (((y >> 7) << 3) + (x >> 7));
if (bgnm == 1) global_index_real += yang_ix_plus;   // yang_ix << 5, yang_ix = 0..3
...
yang_timer--;                       // reload 4
if (!yang_timer) { yang_timer = 4; yang_ix = (yang_ix + 1) & 3;
                   yang_ix_plus = yang_ix << 5; }
```

`yang_ix_plus` steps by 32 - exactly one 8x4 page - every 4 game frames, so BG1 cycles
through four pages at 15 fps. Page 0 is the masked layer (`bgtex_stage_gbix[10][1] ==
0xFFFFFFFF`, all 32 slots); pages 1-3 are the first 96 chunks of `ppgRwBgList`, in
stream order, at the *same* 32 grid slots. The animation is the rain and the neon
flicker on the signage.

So the correct accounting for stage 10 is `32 (BG0) + 4 x 32 (BG1) = 160`, not
`64 + 96 slotless`. `decode_stages.py` encodes this as `ANIM_PAGES = {10: (1, 4)}`
(layer index, page count) and writes `layer1_p1..p3.png` and `assembled_p1..p3.png`
alongside the static ones; each of those 96 chunks is tagged `role: "layer1"` with a
real `slot` and a `page`, not `role: "rewrite", slot: null`.

Stage 10 is the only stage that works this way. The other `tokusyu_stage` values are
per-slot substitution: 1 = stage 3 (`rw30`/`rw31` lightning script, `stage03rw_data_tbl`
naming 13 slots), 3 = stage 19 (`rw190`, `stage19rw_data_tbl`, 4 slots), 4 = stage 7
(Akane), 0 = the generic `bgrw_on` / `bgrw_data_tbl` path. Those genuinely have no
fixed slot and stay `role: "rewrite"`.
- Stage 7 alone consumes 3 further chunks for the Akane sprite (`bg.c:328-340`), via
  `ppgAkaneList` with its own `ppgSetupPalChunk`. These are the 3 chunks in
  `stage07.ppg` with `transNums == 0`, and they are 256x256, not 128x128 - the only
  non-tile-sized `pTEX` chunks in the stage set.

### `--mode direct32` needs one engine fix: the GS alpha convention

Symptom: every repacked stage draws at roughly **half brightness**, blended toward the
backdrop. Measured on Yang's stage, sampling the same sign panel and glyph ink in a
screenshot and in the source page, the game/source per-channel ratio is
`0.43 / 0.52 / 0.53` (panel) and `0.46 / 0.50 / 0.57` (ink) - a uniform ~0.5x, not a
channel swap, with the residual bias coming from the dark blue-grey behind it.

Cause, `flPS2ConvertContext` (`flps2vram.c:1115-1131`):

```c
if (lpSrc->bitdepth == 4) {
    if (direction == 0) {
        if (a == 0xFF) { a = 0x80; }            /* GS: 0x80 == fully opaque */
        else if (a != 0) { a >>= 1; ... }
```

That is correct for the real GS. But both PC renderers upload PSMCT32 straight as
8-bit-per-channel RGBA (`GL_RGBA8` / `SDL_GPU_TEXTUREFORMAT_R8G8B8A8_UNORM`), and
`frag_direct.glsl` uses alpha as plain 0..1 coverage against
`SRC_ALPHA`/`ONE_MINUS_SRC_ALPHA`. So an opaque texel arrives as `0x80` = 0.502 and
draws at half coverage.

**No stock asset reaches this code.** Every stock texture is PSMT4/PSMT8 and takes the
`flMemcpy` path with no conversion at all; their palettes are PSMCT16 (bitdepth 2),
which the `bitdepth == 4` branch skips. 32-bit chunks are the only thing that hits it,
so this is a latent bug that only a `direct32` repack can expose.

There is **no data-side workaround**: the mapping sends every input alpha to `<= 0x80`,
so no file content can produce full coverage. The fix is in the engine - expand alpha
back after the conversion, in the PSMCT32 case of `flPS2ConvertTextureFromContext`:

```c
u8* alpha = (u8*)tcon.ptr + 3;
for (i = 0; i < tcon.width * tcon.height; i++, alpha += 4)
    *alpha = (*alpha >= 0x80) ? 0xFF : (u8)(*alpha * 2);
```

Applied in the local 3sx checkout. Like the pool bump it is uncommitted, so it does not
survive a `git checkout` there.

`--mode indexed` is unaffected (8bpp + PSMCT16 palette), but it caps at 2x.

### Undrawn area is normal
A `pTEX` chunk's trans records typically do not cover the whole tile: across the stage
set, 41-90% of tile area is covered. Uncovered pixels are simply never drawn, so they
are correctly written as fully transparent. This is a property of the format, not a
decode failure.

### Health check (`tools/verify_decoded.py`)
Over all 40 placed layer sheets:

- **0.000% `(0,247,0)` filler on every sheet.** That colour is the unused-slot filler
  in bank 0 of `bg000.bin`; it floods any sheet decoded with a single wrong palette
  bank. Its total absence is strong evidence the trans-table palette lookup is right
  for every stage, not just stage00.
- Seam energy across 128px tile boundaries vs. inside tiles has a mean ratio of ~1.05
  (range 0.68-2.46), i.e. tile edges are statistically indistinguishable from tile
  interiors - the tiles line up. The high outlier, `stage10/layer1` (2.46), is page 0
  of the 4-page animated rain layer: only ~22k of its 512k pixels are drawn, so few
  pixel pairs land on a boundary at all and the ratio is dominated by noise (its
  sibling pages score 0.00, 0.00 and 0.90 on the same measure).

This is the **1x decode** health check. For the seam behaviour of the *upscaled*
output - the thing that motivated upscaling whole pages instead of tiles - see
`docs/upscaling-notes.md`.
- Low distinct-colour counts (23-90) occur on flat backdrop/overlay layers and are
  expected.

### Alpha
Chunk and layer PNGs now carry real alpha: the ARGB1555 alpha bit, plus full
transparency for area no trans record covers. The earlier `work/upscaled_test/`
reference renders forced alpha to 255 for viewing; on drawn pixels the new output is
byte-identical to them.

## Upscaling and the renderer - what actually has to change

### The draw call
`ppgWriteQuadUseTrans` (`PPGFile.c:239-370`) is the stage draw path. Texture
coordinates are **normalized**, never texel-based:

```c
ppgwf = ppg->width;                 // header width, in 16px UNITS
qvtx[0].s = sadd + (sx / ppgwf);    // PPGFile.c:352
qvtx[0].x = pos->x + (sx * pxs / ppgwf);   // screen geometry, same divisor
```

and the non-trans path (`PPGFile.c:381-396`) simply sets `s,t` to 0.0/1.0.

So screen geometry and UVs both depend only on the header's **unit** counts, never on
texel counts. But texel dimensions are not independent: `ppgSetupContextFromPPG`
(`PPGFile.c:1171-1173`) hard-derives them:

```c
bits->width  = ppg->width  * 16;
bits->height = ppg->height * 16;
```

**Conclusion: you cannot raise resolution without raising `width`/`height`.** Scale
those by N and the pixel data by N and everything else - UVs, geometry, sub-rect
placement - is invariant, because every formula divides by the same header field.
No coordinate fix-up is needed.

> **Correction (important).** The original version of this section concluded "no engine
> change is needed". That was wrong, and it was the wrong question. UVs and geometry
> are indeed invariant, but the frame is rasterized into a **fixed 384x224 canvas**, so
> extra texels are discarded before they ever reach the screen, and three PS2-sized
> memory budgets reject large textures outright. See "The 384x224 canvas" and "Engine
> memory budgets" below. Texture work alone changes nothing on screen.

### The catch: GL_NEAREST everywhere
`Renderer_CreateTexture` -> `OpenGLRenderer_CreateTexture`
(`opengl_renderer.c:232-296`) uploads 8bpp textures as `GL_R8UI` with
`GL_TEXTURE_MIN_FILTER`/`MAG_FILTER` = `GL_NEAREST` (`:286-287`), and
`frag_palette_8.glsl` resolves them with `texture(uIndexTex, vTexCoord).r` into a
palette `texelFetch`. Integer samplers **cannot** be linearly filtered, and the
direct path is pinned to `GL_NEAREST` too (`:500-507`).

The engine therefore does no smoothing of its own: **whatever PNG you hand back is
literally what is displayed.** All smoothing must come from the external upscaler.

### The palette ceiling, and how to step over it
Staying 8bpp indexed keeps you inside the CPS3 palette model: 64 colours per bank,
and the trans-record fields cap the grid (`iPoint` is u8 over a `width_units` grid,
so total units <= 256; `cofsXY` nibbles cap a sub-rect at 16 units). For a 128x128
tile that is **2x maximum**, and an upscaler's new intermediate tones get quantized
straight back out.

The clean escape is in the file format itself. `ppgSetupTexChunk_2nd`
(`PPGFile.c:718-720`):

```c
if ((ppg->pixel & 3) < 2) { hnof->b16[1] |= 0x4000; }
```

The "this chunk is palettized" flag is derived **per chunk from the file's own pixel
field**. Rewriting a chunk as pixel mode 3 (PSMCT32, `formARGB` 0x8888) makes the
engine stop consulting a palette entirely - no code change. With `transNums` set to 0
the chunk draws as one full-tile quad, which is geometrically identical to the union
of its sub-rect quads, and the undrawn area is carried as alpha 0, which
`frag_direct.glsl` `discard`s. Behaviour is preserved exactly; the ceiling disappears.

Cost: 32bpp is 4 bytes/texel. stage10's 160 chunks at 4x would be ~160 MB of texture
data for one stage. Mode 2 (PSMCT16, ARGB1555) halves that at the price of 5-bit
channels.

> **Correction.** "The ceiling disappears" is true of the *format* only. In practice
> direct32 is bounded by the 10 MB texture pool (`foundaps2.c:58`) and the 384 KB
> per-chunk buffer (`main.c:89`), both of which it blows at 2x already. See "Engine
> memory budgets". `--mode indexed` at 2x is the only configuration that runs unpatched.

## The 384x224 canvas - the real gate on HD textures

Both render backends draw the entire frame into a **fixed 384x224 offscreen canvas**
and then point-scale that canvas to the window:

- `opengl_renderer.c:509-517` creates the colour and depth FBO attachments at 384x224,
  and `:641` draws with `glViewport(0, 0, 384, 224)`.
- `sdl_gpu_renderer.c:19-20` `CANVAS_WIDTH 384` / `CANVAS_HEIGHT 224`, used for the
  canvas and depth textures at `:618-632`.

A background tile covering ~128 canvas pixels samples a 512x512 texture with
`GL_NEAREST` and **throws away 15 of every 16 texels**. Upscaled textures therefore
produce a bit-identical image to stock. This was confirmed on screen: a 2x pack loaded,
uploaded and rendered with no visible change whatsoever.

### The prototype: `CANVAS_SCALE`
Geometry is already expressed in *logical* 384x224 units and converted to NDC by
dividing by those constants (`sdl_gpu_renderer.c:292-305`, `x /= 384`). NDC is
resolution-independent, so the canvas can simply be made bigger:

```c
#ifndef CANVAS_SCALE
#define CANVAS_SCALE 4          // 1 = stock
#endif
#define CANVAS_WIDTH  (384 * CANVAS_SCALE)
#define CANVAS_HEIGHT (224 * CANVAS_SCALE)
```

Applied in `sdl_gpu_renderer.c:22-26` and `opengl_renderer.c:234-238` (plus the two
`glTexImage2D` calls and `glViewport`). **No game logic changes** - the ~200 other
hardcoded `384` sites are logical units and must stay as they are.

Overridable at configure time without editing sources:
`-DCMAKE_C_FLAGS="-DCANVAS_SCALE=1"` reproduces stock exactly, which is the clean A/B.

### Current consequences
- Characters use HD pixels when their sidecar is enabled. UI retains
  their stock art on the larger canvas.
- The half-texel inset compares transformed logical geometry with logical w/h;
  increasing canvas resolution alone does not turn it on. With HD atlases the
  inset is half a physical texel, normalized by the physical page side.
- Scanline and integer-scale presentation assumptions still need review. The
  test profile uses nearest scaling with scanlines disabled.

## Engine memory budgets - three PS2-sized ceilings

All three reject HD textures, and they fail in this order as size grows:

| budget | where | stock | holds |
|---|---|---|---|
| master pool | `foundaps2.c:51,57` | `0x1800000` (24 MB) | everything below |
| texture pool (`sysmemmgr`) | `foundaps2.c:58-60` | `0xA00000` (10 MB) | decompressed texture bytes the renderer uploads from |
| ramcnt heap | `main.c:92-94` (`flGetSpace()`) | ~13 MB remainder | whole `.ppg`/`.bin` files as loaded |
| chunk decompress buffer | `main.c:89` | `0x60000` (384 KB) | **one** decompressed chunk |

Failure signatures, so they are recognisable next time:
- ramcnt exhausted -> `Fatal error: メモリの確保に失敗しました。` ("memory allocation
  failed"), `ramcnt.c:194`.
- texture pool exhausted -> `Fatal error: ERROR flPS2GetSystemMemoryHandle flps2etc.c`,
  `flps2etc.c:92`.

### Measured requirements
Per stage, texture-pool bytes = sum over chunks of `w*h*bytes_per_texel`:

| stage | 1x 8bpp (stock) | 2x 8bpp | 2x 32bpp | 4x 32bpp |
|---|---|---|---|---|
| stage01 (Alex) | 0.5 MB | 2 MB | 8 MB | 32 MB |
| stage02 (Ryu) | 0.8 MB | 3.2 MB | 13 MB | 52 MB |

Largest single decompressed chunk: 256 KB at 2x/32bpp, **1 MB** at 4x/32bpp (and 4 MB
for stage07's 256x256 Akane chunks at 4x).

### The patches (all `#ifndef`-guarded, overridable from the command line)
```c
foundaps2.c:29   HD_MASTER_POOL   0x14000000  // 320 MB  (stock 0x1800000 = 24 MB)
foundaps2.c:32   HD_TEXTURE_POOL  0x8000000   // 128 MB  (stock 0xA00000  = 10 MB)
main.c:82        HD_PPG_DECBUF    0x800000    // 8 MB    (stock 0x60000   = 384 KB)
```
The master pool must exceed texture pool + ramcnt heap, since `system_memory_size` is
carved out of it by `flAllocMemoryS`. Memory is reserved up front whether used or not.

## Historical stage scale comparison: 2x vs 4x

The current test build uses 4x direct32 stages. This comparison documents the
original indexed PPG constraints, not the character sidecar format.

The pipeline is scale-agnostic; `ppg_repack.py` infers the factor from the PNG
dimensions and `check_upscaled.py` enforces one factor per stage. What changes is which
mode is available and whether the engine needs patching.

| | 2x indexed | 2x direct32 | 4x direct32 |
|---|---|---|---|
| texture bytes vs stock | 4x | 16x | 64x |
| runs on **stock** engine | **yes** | no (texture pool) | no (all three) |
| colour fidelity | quantized to 64-colour banks, ~0.8% mean error | exact | exact |
| palette animation preserved | yes | no (colour baked in) | no |
| max scale | **2x is a hard cap** | - | - |

Why 2x is the cap for `indexed`: the trans record's `iPoint` is a u8 over a
`width_units` grid, so total units <= 256; a 128x128 tile at 2x is exactly 16x16 = 256
units, right at the limit. `cofsXY` nibbles also cap a sub-rect at 16 units.

**2x indexed is the only configuration that runs on an unmodified 3sx**, which matters
if the pack is ever distributed. 4x direct32 is the quality ceiling but hard-requires a
patched engine (both the canvas and all three memory constants).

Measured re-quantization cost for 2x indexed on stage01: 6.2% of drawn pixels bit-exact,
**mean |dR|+|dG|+|dB| = 6.11 out of 765** (~0.8%, about 2 levels per channel). Visually
this is slight banding on smooth gradients, not wrong colour.

## PSMCT32 payload byte order - BGRA, and skip the endian swap

`direct32` output must be **BGRA**, not RGBA, and must set bit `0x4` in the `pixel`
field. Two independent transforms are involved:

1. `ppgSetupContextFromPPG`'s `0x8888` case declares B at bit 0, G at 8, R at 16, A at
   24 - i.e. BGRA bytes in little-endian memory. `flPS2ConvertTextureFromContext`'s
   `SCE_GS_PSMCT32` case (`flps2vram.c:1030-1047`) overrides the *destination* to
   `rs = 0`, `bs = 0x10`, producing RGBA for the GL upload. The conversion is therefore
   exactly an R/B swap, so the source must be BGRA.
2. `ppgChangeDataEndian` (`PPGFile.c:654-673`) byte-swaps **every u32** unless
   `dendL != 0`, where `dendL = ppg->pixel & 4` (call site `PPGFile.c:1080`). Setting
   `pixel = 3 | 4` makes it return early and leaves the payload alone.

Getting either wrong rotates the channels to `(A,B,G,R)`. Diagnosed from a screenshot:
source sky `(245,144,77)` orange rendered as `(255,77,144)` pink, and **alpha became the
original red channel**, so dark areas turned semi-transparent and the whole stage looked
washed-out and dark. Measured on screen as `(119,35,66)` = the pink times the stage's
~0.5 vertex colour. Fixed in `to_bgra32()`.

`roundtrip_check.py` undoes the same swap when comparing, so the round-trip test stays a
real check rather than a self-consistent one.

## Verified end to end, on screen

4x `direct32` + `CANVAS_SCALE 4` + the three memory patches renders correctly in game:
`stage01` (Alex), `stage02` (Ryu) and `Bonus00` (car smash), 111 chunks total, all
round-tripping pixel-identical to the source PNGs. Archive +27.7 MB, with 1532/1532
untouched entries verified byte-identical.

## Repack pipeline

### Export naming contract
The upscaler works on **pages**, not tiles - one 8x4 grid of 128px chunks cropped
to the occupied slots, which is the unit `Bg_Disp` actually draws
(`bg.c:623-625`). Upscaling tiles individually forced the network to invent every
128px edge and left a measurable seam lattice; see `docs/upscaling-notes.md` for
the before/after numbers.

```
work/decoded/<stage>/chunk_NN.png            decode_stages.py
  -> work/for_upscale/<stage>/page_L<l>_p<p>.png   export_for_upscale.py
     work/for_upscale/<stage>/chunk_NN.png         (slotless chunks only)
  -> work/upscaled/<stage>/<same names>            upscale.py  (or any upscaler)
  -> work/tiles/<stage>/chunk_NN.png               cut_pages.py
  -> work/repacked/<stage>.ppg                     ppg_repack.py
```

The plan lives in `tools/stage_pages.py` and is derived entirely from the
manifest's `role`/`slot`/`page` tags, so it stays correct as the role assignment
improves. `cut()` reads the scale off the page image rather than being told it,
which makes the 1x self-test and a 4x run the same code path:

```sh
python3 tools/cut_pages.py --src work/for_upscale --out work/tiles_1x \
                           --verify-against work/decoded
```

reproduces every one of the 1122 chunk PNGs byte for byte.

One integer scale factor per stage. Alpha 0 must stay 0. Per-stage header
fields, trans records, grid slots and palette pairing live in
`work/decoded/<stage>/manifest.json`, which `ppg_repack.py` consumes.

### Chunk roles in the rebuilt stream
Chunks keep their original stream order. `manifest.json` tags each one `layer0`/
`layer1`/`layer2` with a grid slot, or `rewrite` / `extra` with `slot: null` -
rewrite chunks (`rewrite_scr`) and stage07's 3 Akane chunks have no static slot, so
position in the stream is the only thing that identifies them and it is preserved
exactly. Stage 10's 96 rewrite chunks are the exception: they carry a real `slot` plus
a `page` (1-3) because they are extra pages of BG1, see the stage 10 section above.
Roles are descriptive metadata only - `ppg_repack.py` keys off stream position, so
retagging a chunk cannot change the rebuilt file. Non-`pTEX` chunks are copied verbatim: across all 21 stage files there is
exactly one, the `pPAL` in `stage07.ppg`.

### AFS alignment - measured, not assumed
Over the real index: all 1535 entries are 0x800-aligned; for all 1534 consecutive
pairs the next entry starts at exactly the next 0x800 boundary (no slack); the data
region starts at `align(8 + count*8 + 8, 0x800)` = 0x3800; the attribute table sits
at the 0x800 boundary after the last blob with size `count * 48`; the file is padded
to a 0x800 multiple. `afs_rebuild.py` regenerates the whole index so growth in any
entry shifts every later entry correctly.

Attribute records are copied verbatim. 3sx reads only the NUL-terminated name
(`afs.c:153-162`); the 16 bytes after it are a fixed 2004-03-13 timestamp plus a u32
whose stock values are a strided copy of the index table (record 0 holds the entry
count, record 1 the first offset, record 2 the first size...) rather than per-entry
sizes. There is no correct value to recompute, so they are left untouched.

### Round-trip results (1x, un-upscaled)
- `--mode indexed` and `--mode direct32`, all 21 stages: every chunk decodes back
  **pixel-identical** to its source PNG, including stage07's 256x256 Akane chunks and
  stage10's 160 chunks.
- `afs_rebuild.py` with **no** replacements reproduces the 642,492,416-byte original
  **byte-for-byte** (`cmp` clean). That validates the index, alignment, attribute and
  padding logic end to end.
- With all 21 stages replaced at 1x direct32 the rebuilt AFS is +1,114,112 bytes and
  re-parses cleanly.

## Character sprites - scoping (Ryu, CHAR_RYU = 2, constants.h:15)

### Ryu's AFS entries
| idx | name | size | role |
|---|---|---|---|
| 1365 | `pl02plm.bin` | 2,048 | 16 x 64 x u16 - the `COL_x1000` "metamor" palette (type 3) |
| 1468 | `pl02.bin` | 1,630,652 | OBJ piece lists + CG data |
| 1470 | `pl02pl.bin` | 3,584 | 28 x 64 x u16 - the costume palette set (type 1) |
| 1471 | `PL02.bd` | 190,048 | frame/animation tables |

### color_file types 1 / 3 / 10 vs the type-2 background path
Type 2 (`color3rd.c:262-278`) is a flat `u16` array copied into `ColorRAM[data]` -
that is the whole mechanism, which is why stages were resolvable offline.

**Type 1** (`color3rd.c:180-260`) reads `COL { u16 col[2][28][64]; }`
(`color3rd.c:22-24`). The 28 dimension is *not* 28 costumes:
- `col[s][Player_Color[id]][*]` -> `ColorRAM[id*16]` and `ColorRAM[id*16+8]`: the
  selected costume bank.
- `col[s][16..21][*]` -> `ColorRAM[id*16+1 .. +6]` and `+9 .. +14`: six **shared**
  banks, the same for every costume (effects, flashes).
- `col[0][22][*]` read as **256 consecutive colours** -> `ColorRAM[502]` (P1) or
  `ColorRAM[506]` (P2): a 4-bank block.

The `[2]` first dimension is two colour sets, only both used when `My_char[id] == 0`;
otherwise `col[0]` is mirrored into both. Note `pl00pl.bin` is 7,168 bytes (= 2 x 28 x
64 x 2) but `pl02pl.bin` is 3,584 (= 1 x 28 x 64 x 2), so **the `[2]` dimension is not
present in every character's file** - size must be checked, not assumed.

**Type 3** (`color3rd.c:280-...`) reads `COL_x1000 { u16 col[2][16][64]; }` into
`hi_meta[][]` via `metamor_color_store` - the Twelve/morph path, and the source of
`plNNplm.bin`. Type 10 is the `cse*` bank path.

Palette colours go through `palConvSrcToRam` exactly as backgrounds do, so the
**ABGR1555 / LE finding carries over unchanged**.

### Is a "default" costume pickable offline? Yes.
`Player_Color[2]` is `s8` (`workuser.h:167`), set from the button used to select the
character; the values that appear in `next_cpu.c:1206-1238` are 0, 1, 7, 8, 10, 11 -
i.e. 12 alternates indexed 0-11 inside the 28-bank array, with banks 16-21 shared and
22-25 the 256-colour block. `Player_Color = 0` is the LP/default costume and lives at
a fixed offset in the file, so **offline decoding does not need game state**: bank 0
is the canonical default and banks 1-11 are the alternates, all directly addressable.
This is a materially better situation than the earlier note suggested.

### Original character format differs from backgrounds

The example below describes the original 1x path. The current initializer uses
`16 * hdCgScaleForCache(mt->id)` for the PPG width and height.
There is **no PPG chunk stream** for character sprites. `mlt_obj_trans_init`
(`mtrans.c:2035-2064`) *synthesises* a `PPGFileHeader` in local storage:

```c
ppg.width = ppg.height = 16;      // -> a fixed 256x256 texture page
ppg.compress = 0;                 // raw
ppg.formARGB = 0x1555;
ppg.transNums = 0;
ppg.pixel = 0x81;                 // 8bpp indexed  (0x82 = 16bpp for mode 4)
ppgSetupTexChunkSeqs(&mt->tex, &ppg, adrs, mt->mltgidx16, mt->mltnum, mt->attribute);
```

Sprites are a **runtime texture atlas**: 16x16 and 32x32 CG tiles are allocated into
256x256 pages by an LRU cache (`get_mltbuf16` / `get_mltbuf32`, `mtrans.c:1667-1740`,
which hard-fail with "CG cache is full" when exhausted). A frame is a *list of tiles
with offsets*, not a rectangular image. Nothing in the AFS is a finished sprite.

### `pl02.bin` structure (measured)
- Header: 939 little-endian u32 offsets (first offset 3756 = 939 * 4, so the table
  self-terminates), strictly monotonic, covering the whole file.
- Records 0-937 are small (10-114 bytes, modal 10/82/90/74) - **OBJ piece lists**.
  Record 1 decodes cleanly as `u16 count = 11` followed by 11 x 8-byte pieces
  `(s16 x, s16 y, u16 attr, u16 index)`, where the last field across the 11 pieces is
  `7,9,8,1,3,5,10,11,2,4,6` - a permutation of 1..11, i.e. a draw order. 2 + 11*8 = 90
  bytes exactly.
- Record 938 is 1,557,916 bytes - the bulk CG data.

### Resolved addressing and export

`pl02.bin` contains both piece lists and the tile table; `PL02.bd` is not involved
in sprite decoding. Record 938 is 92 bytes, not the whole remainder of the file.
The Morton traversal is used by the PC character path despite the `dc_ghost.c`
wrapper name. Batch decoding and frame assembly are implemented in
`cg_decode_all.py`. Frames provide neighbor context for upscaling; the HD packer
recuts indexed tiles with an explicit shared-tile/overlap policy.

## Character CG addressing - resolved

The earlier guess that `PL02.bd` held the CG index was wrong. **Everything is inside
`plNN.bin`**; `PL02.bd` plays no part in sprite pixel decoding.

`texgrpdat[100]` (`texgroup_data.c:10`) gives the layout per character. Ryu is entry 3:

```c
[3] = { .num_of_1st = 2592, .apfn = 1468, .mode = TEXGROUP_MODE_CHARACTER,
        .to_tex = 0x11C7C, .to_chd = 0x178F38 },   // pl02.bin
```

and `texgroup.c:140-141` splits the loaded file with those offsets:

```c
curr->lds->texture_table = ldadr + bsd->to_tex;
curr->lds->trans_table   = ldadr;              // i.e. the file base
```

So `pl02.bin` is three concatenated regions:

| range | contents |
|---|---|
| `0` .. `0x11C7C` | **trans_table**: 939 LE u32 offsets, then 939 piece lists |
| `0x11C7C` .. `0x178F38` | **texture_table**: 6648 LE u32 offsets, then `TEX` blobs |
| `0x178F38` .. EOF | character data (`to_chd`) |

The offset-table entry count is self-describing: `n = *(u32*)trans_table / 4`
(`mtrans.c:2170`), which is 939 here.

### Piece list
`mtrans.c:2176-2179`: `u16 count`, then `count` x `TileMapEntry`
(`structs.h:1582-1587`, 8 bytes):

```c
typedef struct { s16 x; s16 y; u16 attr; u16 code; } TileMapEntry;
```

List 1 parses as count 11 followed by 11 x 8 bytes = 90 bytes exactly, matching the
record length with nothing left over.

### Tile
`texptr = texture_table + ((u32*)texture_table)[code]` (`mtrans.c:2185`), and
`TEX { u8 wh; u8 dat[]; }` (`structs.h:1154-1157`) - a **one-byte** header:

```c
wh   = (texptr->wh & 3) + 1;      // mtrans.c:2187
size = (wh * wh) << 6;            // mtrans.c:2188
lz_ext_p6_fx(&((u8*)texptr)[1], mt->mltbuf, size);   // mtrans.c:2193
```

| `wh` | size | pixels |
|---|---|---|
| 1 | 64 | 8x8 |
| 2 | 256 | 16x16 |
| 4 | 1024 | 32x32 |

The upper bits of the same byte carry a position adjustment
`dd = (((wh & 0xE0) << 5) - 0x400) | (((wh & 0x1C) << 6) - 0x100)` (`mtrans.c:2186`)
that is folded into the tile's `attr`, not into the pixel decode.

### `lz_ext_p6_fx` - the CG codec
`mtrans.c:1935-1983`. A byte-oriented LZ77 with four opcodes on the top two bits:

| top bits | meaning |
|---|---|
| `00` | literal, the byte itself (so 0x00-0x3F) |
| `01` | short back-reference: distance `(b & 0x3F) >> 2` + 1, length `(b & 3) + 2` |
| `10` | long back-reference, 2 bytes: distance `(v >> 6)` + 1, length `(v & 0x3F) + 2` |
| `11` | packed nibbles: `flg = b & 0x30`, then `(b & 0xF) + 2` source bytes each emitting `flg\|hi` and `flg\|lo` |

**Every output byte is 0-63**: literals are capped by the opcode, and the nibble path
ORs a 2-bit bank flag into a 4-bit value. So a CG tile is 4bpp-with-bank-select stored
as 8bpp indices into exactly **one 64-colour CPS3 bank** - which is why `palt` is only
2 bits. `lz_ext_p6_cx` (`mtrans.c:1985-2033`) is the identical codec writing RGB
directly through `ColorRAM[palt]`.

### Palette for a tile
**Character palettes are ARGB1555 - NO red/blue swap - unlike `bgNN0.bin`, which is
ABGR1555.** This is the opposite convention and must not be carried over from the
background path. Proof: bank 0 of `pl02pl.bin` read unswapped gives a clean 7-step skin
ramp at 0x22-0x28 ((247,198,148) -> (231,165,115) -> (214,148,99) -> (198,132,82) ->
(181,99,49) -> (165,82,33) -> (148,49,16)) plus crimson at 0x20-0x21 (181,16,49) /
(115,0,33) - Ryu's headband. Read swapped, that entire skin ramp renders as a blue ramp
and the frame comes out 43% blue with no flesh tone anywhere.

The palette *index* comes from the work object, not the piece: `mlt_obj_trans_ext`
passes `palo | (flip bits)` to `seqsStoreChip` (`mtrans.c:484-493, 505-514`) where
`palo = wk->colcd` (`mtrans.c:420`), and `seqsStoreChip` resolves it with
`ppgGetUsingPaletteHandle(NULL, attr & 0x1FF)`. The `dd` field derived from the `wh`
byte is **not** part of the palette code in this path; `attr & 3` on the piece is only
used for cache keying in the *upload* pass (`mtrans.c:2191`). For player 0 at
`Player_Color = 0` the effective bank is ColorRAM[0] = `col[0][0]` = the first 64 u16 of
`plNNpl.bin`.

With `Player_Color = 0` and player id 0,
`init_trans_color_ram` type 1 (`color3rd.c:180-206`) writes `col[0][Player_Color]`
into `ColorRAM[id*16]`, so **`palt` 0 is the default costume bank = the first 64 u16
of `plNNpl.bin`**, read LE/ABGR1555 exactly as backgrounds are. Banks 1-11 are the
costume alternates at the same stride.

### Pixel order: Morton, not linear
CG tile payloads are Morton-ordered and must be passed through `dctex_linear` - see
"CG tiles ARE Morton-swizzled" below. This was the cause of a first decode attempt
that produced banded blue/white noise despite every container-level check passing.

### Verified on Ryu
`pl02.bin` piece list 1, all 11 pieces: the LZ stream terminates **exactly** at the
requested size for every piece (108, 20, 163, 388, 545, 263, 92, 4, 355, 298, 369
input bytes producing exactly 1024/64/1024/1024/1024/1024/256/64/1024/1024/1024
pixels), and the maximum index in every tile is <= 63 - never crossing a bank. Partial
opacity (79/1024 to 916/1024) is consistent with sprite art rather than noise.
Composited at the piece offsets the frame is 79x149 px; list 2 gives 99x144 - correct
proportions for a 3rd Strike character. Renders:
`work/upscaled_test/10_pl02_list1_piece*.png` and `11_ryu_list*_y*.png`.

## Frame composition - one list per frame, cumulative offsets

### A piece list IS a whole frame
`mlt_obj_trans_ext` (`mtrans.c:381-412`) takes `n = wk->cg_number` from the work object
and resolves exactly **one** piece list:

```c
n = wk->cg_number;
i = obj_group_table[n];
n -= texgrpdat[i].num_of_1st;
trsbas = (u16*)(texgrplds[i].trans_table + ((u32*)texgrplds[i].trans_table)[n]);
count = *trsbas;
```

There is no multi-list compositing - nothing like the stage BG's 1-3 layers. So 11
pieces genuinely is Ryu's complete standing frame, and the earlier fragmentation was a
real bug, not a partial render.

### Offsets are cumulative deltas, not absolute positions
This was the bug. `mtrans.c:455-467`:

```c
x = y = 0.0f;
while (count--) {
    if (attr & 0x8000) { x += trsptr->x; } else { x -= trsptr->x; }
    if (attr & 0x4000) { y -= trsptr->y; } else { y += trsptr->y; }
```

Each piece sits at the **running sum** of all preceding offsets. `attr` bit 0x8000 is
horizontal flip, 0x4000 vertical (`flptbl[4] = {0, 0x8000, 0x4000, 0xC000}`,
`mtrans.c:40`, indexed by `wk->cg_flip ^ wk->rl_flag`, so 0 = unflipped).

**For an offline top-down raster, BOTH accumulators run negated and the accumulated y
is the tile's TOP edge.** In game coordinates the unflipped branch is `y += trsptr->y`
and `seqsStoreChip` treats y as the *bottom* (`v[0].y = y`, `v[1].y = y - h`,
`mtrans.c:1606-1610`) - but `mlt_obj_matrix` transforms before rasterizing and
characters are positioned from the ground up, so the axis is inverted end to end.

Established by search, not by reading: over {field order, x sign, y sign, y edge,
dw/dh order} on frames 1/2/300/700, exactly one combination yields a **single connected
component on all four** (100% of drawn pixels); the next best leaves 4-24% detached.
Flip state cannot be used to discriminate here - flips are rigid transforms, so
connectivity is invariant under all four. The same accumulation appears in the
bounding-height helper at `mtrans.c:366-372` (`height = height + trsptr->y`), which is
corroborating evidence that these are deltas.

Treating them as absolute coordinates scattered the tiles across a bounding box with
~90% mutual overlap. With the accumulation applied, overlap drops to **5%** - the pieces
tile the sprite almost exactly, which is what a correct mosaic looks like.

### Tiles are padded into a square slot
A tile's storage is `wh*8` square (`size = (wh*wh) << 6`), but only the top-left
`dw x dh` is real image (`mtrans.c:470-471`):

```c
dw = (texptr->wh & 0xE0) >> 2;      // = ((wh >> 5) & 7) * 8
dh = (texptr->wh & 0x1C) * 2;       // = ((wh >> 2) & 7) * 8
```

So the single `wh` byte packs three fields: bits 0-1 storage square, bits 2-4 drawn
height/8, bits 5-7 drawn width/8. Non-square draws are common - e.g. `0x2F` is stored
32x32 but drawn 8x24, `0x53` stored 32x32 drawn 16x32.

**Verified:** across 300 tiles, of 54,080 pixels lying outside `dw x dh`, **zero** are
non-transparent. The padding is genuinely empty.

### Result
All four test frames composite to a **single connected figure** (100% of drawn pixels in
one 4-neighbour component):

| list | size | coverage |
|---|---|---|
| 1 | 80x103 | 52% |
| 2 | 80x104 | 52% |
| 300 | 144x64 | 38% |
| 700 | 87x112 | 45% |

Renders: `work/upscaled_test/12_pl02_frame*.png`, plus 5x nearest-neighbour blowups as
`13_pl02_frame*_x5.png` (the raw frames are too small to judge on screen).

Connected-component count is the right metric here - it is colour-independent and
cannot be fooled the way a subsampled alpha mask can.

### Horizontal frames are normal, not a rotation artifact
**54% of the 938 lists in `pl02.bin` are wider than tall.** Lists 294-303 are a single
animation whose bounding box collapses in height at near-constant width -
133x104, 144x101, 144x88, 136x80, 144x72, 144x71, 144x64, 139x56, 138x64, 140x62 -
then list 304 resets to 71x112 (upright) for the next animation. That is a
knockdown/fall sequence drawn frame by frame.

Nothing in the CG format encodes rotation: the piece `attr` carries only the two flip
bits (0x8000 / 0x4000) and a 2-bit palette field, and there is no angle anywhere in
`TileMapEntry` or the `wh` byte. A wide frame is therefore always source art, never a
decode artifact. (A decode-side transpose would also corrupt *every* frame, not one -
the upright frames 1, 2 and 700 decoding correctly rules it out on its own.)

Strip render: `work/upscaled_test/14_pl02_knockdown_strip.png` (frames 294-304,
bottom-aligned).

### Still unconfirmed
- Draw order within a list (currently file order). Overlap is ~5%, so it rarely matters,
  but it will for overlapping limbs.
- `attr` bits beyond 0x8000 / 0x4000 / 0x0003.
- Which facing the art is authored for (all four flip states are self-consistent).

## Atlas padding and sampling - there is NO padding

### The packer
`njReLoadTexturePartNumG` (`dc_ghost.c:282`) -> `ppgRenewDotDataSeqs`
(`PPGFile.c:749-861`). Destination addressing uses two macros (`PPGFile.c:18-19`):

```c
#define CODE_0(val) ((val & 0xF0) << 8) + ((val & 0xF) << 4)    // 16x16 slots
#define CODE_1(val) ((val & 0x38) << 0xA) + ((val & 7) << 5)    // 32x32 slots
```

`CODE_0` puts slot `v` at `x = (v & 0xF) * 16`, `y = (v >> 4) * 16` in a 256-byte-pitch
page - a **16x16 grid of 16x16 tiles**, exactly filling 256x256. `CODE_1` gives an
**8x8 grid of 32x32 tiles**. The row advance confirms it: after writing 16 bytes the
16x16 case does `dstRam8 += 0xF0` (240), total stride 256; the 32x32 case writes 32
then `+= 0xE0` (224), again 256.

**Tiles are packed edge to edge with zero padding, zero border, zero bleed margin.**
Slots are handed out by an LRU cache in allocation order (`get_mltbuf16` /
`get_mltbuf32`, `mtrans.c:1667-1740`; `cd16 += 1` at `mtrans.c:2199`), so a tile's
neighbours in the atlas are **unrelated tiles from unrelated sprites**.

### CG tiles ARE Morton-swizzled  (corrects the note below)
The loose end flagged below turned out to matter. `dctex_linear`
(`ppgMakeConvTableTexDC`, `PPGFile.c:864-887`) **is** applied to character CG on this
path; decoding tiles as linear rasters is wrong.

Evidence, in order of strength:
1. Sweeping every plausible row stride (1..256) over 150 tiles, the equal-neighbour
   rate falls off **monotonically** with no peak anywhere - the signature of data with
   no 2-D structure at any width. A raster image always peaks at its true stride.
2. Applying the table cuts the transparency-mask perimeter from **71,949 to 22,309**
   across 399 tiles (-69%). Block-reshuffle hypotheses only reached 52,468.
3. The resulting mask shows a clean monotone diagonal body edge instead of scattered
   speckle.

Application (`PPGFile.c:772-816`):
- 8x8 and 16x16: `dst[i][j] = src[dctex_linear[j + (i << 5)]]`
- 32x32: `dst[k] = src[dctex_linear[k]]` for k over the full row-major 32x32

**The earlier claim that `dctex_linear` is "Dreamcast-only" was wrong** and is
withdrawn. It holds for stage backgrounds, which never go through this path - the
stage pipeline is unaffected - but not as a general statement.

### Atlas initialization flag (resolved)
`mlt_obj_trans_init` clears `be` before calling `ppgSetupTexChunkSeqs`.
That setup function sets `tch->be = 1` after creating the page handles.
Thus `ppgRenewDotDataSeqs` can write normally; there is no missing
initialization or decomp gap. See the full
[allocation/upload trace](character-atlas-trace.md).

### The sampler
`seqsStoreChip` (`mtrans.c:1600-1655`):

```c
u = (code & 0xF) * 16;  v = code & 0xF0;          // 16x16 case
const bool scaled = fabsf(screen_w - w) > 0.001f || fabsf(screen_h - h) > 0.001f;
const f32 uv_dx = scaled ? 0.5f : 0.0f;
chip->t[0].s = (u + uv_dx) / 256.0f;
chip->t[1].s = (u + w - uv_dx) / 256.0f;
```

At 1:1 the inset is **zero** and UVs land on exact tile boundaries; combined with
`GL_NEAREST` (`opengl_renderer.c:286-287`) nothing outside the tile is ever sampled.
That is why zero padding is safe in the stock game. The `0.5f` inset is a port-side
half-texel fix applied only when a sprite is drawn scaled.

### Consequences for per-tile upscaling
1. **You must pad yourself.** Upscaling a 16x16 or 32x32 tile in isolation gives the
   upscaler no context past the edge, so it invents one - typically edge darkening or a
   halo. Because tiles that are adjacent *in the sprite* are not adjacent *in the
   atlas*, this appears as a seam every 16 or 32 px across the character at 4x that
   does not exist at 1x.
2. **Pad with real neighbours, not replicate/mirror.** The correct context for a tile
   is the surrounding pixels of the *composited frame*, reachable from the piece list's
   `x`/`y` offsets. Composite the frame, cut each tile with an N-pixel margin, upscale,
   then trim the margin back off.
3. **Watch the `uv_dx = 0.5f` inset.** It is expressed in page-normalized units
   (`0.5 / 256.0`). If the atlas page is enlarged 4x without scaling that constant, a
   scaled sprite gets trimmed by 2 upscaled texels per edge instead of half a source
   texel. That one is a code-side fix, not something the texture pack can correct.

## Batch character decode - Ryu, all frames

`tools/cg_decode_all.py SF33RD.AFS --cg pl02.bin` decodes every animation frame of one
character into `work/decoded/<cg>/frame_NNN.png` plus a `manifest.json`.

**Result for Ryu: 725 frames, 0 errors, 2.3 s.** Frame sizes range 40x22 to 177x160.

### Per-piece flip bits - do not ignore them
`TileMapEntry.attr` carries **per-piece** flip flags, distinct from the object-level
flip: across `pl02.bin`, 181 pieces have 0x8000, 54 have 0xC000, 51 have 0x4000.
`mtrans.c:484-493` passes `palo | ((trsptr->attr ^ attr) & 0xC000)` to `seqsStoreChip`,
which swaps the s/t coordinates - so the tile content **mirrors in place**, with its
position unchanged (the position shift `x - dw*BOOL(...)` uses the *object* attr only).

Ignoring these produced visibly broken frames - 28-33 and 313-319 in particular, where
most pieces carry 0x8000 (frame 314 has 8 of 9). Applying them took the count of frames
below 90% single-component from **3 to 0**.

### Placeholder lists
214 of the 939 lists reference **only tile code 0**, using it as a null; there are just
22 distinct piece-lists among all 214, so they render as one tile repeated. They are
placeholder slots, not frames, and are excluded.

**Tile 0 is not blank** - it is 745/768 opaque pixels of real art - so keeping these
lists yields dozens of identical partial-image files rather than empty ones. That is the
symptom to recognise.

### Frame anchor must be recorded
17 frame pairs decode to byte-identical images while having *different* source lists;
in every case the only difference is the **first piece's x/y**, i.e. the frame's anchor.
Normalising to the bounding box discards it. The manifest therefore stores `ox`/`oy`
(the accumulated coordinates of the bbox corner before normalisation) so sprites do not
shift on repack.

### Sanity pass
Connected-component fraction over all 725 frames: **0 fall below 90%** of drawn pixels
in a single component. No unsupported tiles, no oversized lists.

### Frames are the upscale unit, not tiles
Measured on `pl02.bin`: 939 frames, 8399 piece references, 6648 distinct tile codes.
**5853 tiles (88%) are referenced exactly once**; mean 1.26 refs; only 15 tiles are used
10+ times (code 0, used 214 times, is a blank).

So compositing frames for upscaling and re-cutting tiles afterwards duplicates almost
nothing - worst case the rebuilt texture table grows from 6648 to 8399 entries (+26%).
That is the right trade, because the atlas has **zero padding** (see "Atlas padding and
sampling"), so an isolated tile gives an upscaler no edge context and would seam at every
16/32px boundary. Upscaling the composited frame supplies real neighbour context for free.

### Implemented character pack routes

- The 1x AFS repacker re-swizzles CG tiles and implements the `lz_ext_p6_fx`
  encoder. It preserves piece lists and logical dimensions.
- The HD sidecar packer stores linear indices at 2x or 4x, without changing
  original tile codes or the packed `wh`. The engine allocates physical atlas
  pages and samples them at that density; no expanded Morton table is needed.
- Both preserve the original 64-color banks. The HD route selects each shared
  tile's most-visible occurrence and retains stock pixels in hidden regions.
- Character piece lists are trans tables, but they are not PPG `iPoint` grids.
  Their original dimension fields remain logical units. At 4x a logical 32x32
  tile has a separate 128x128 physical payload.

## Original-format 1x character repack

This section documents `cg_repack.py`, not the HD sidecar route. The original
renderer used w/h both for geometry and a span into a 256x256 page. Increasing
those packed dimensions enlarged fighters. The current renderer separates
logical geometry from physical texture density; use `cg_hd_pack.py` and the
[test-build guide](hd-test-build.md) for HD characters.

### What the repack does change
Only the **texture table**. The trans table is copied byte for byte, so piece
counts, x/y deltas, `attr` (including the flip bits) and tile `code`s are
untouched, `to_tex` cannot move, and frame anchors cannot drift. Only `to_chd`
can shift, and only when re-encoded tiles change the table's size; the tool pads
back to the original length whenever it fits and tells you either way.

Per tile the chain is the exact inverse of `cg_decode_tile`:

```
frame PNG -> cut the piece rect -> un-flip -> quantise to the piece's bank
          -> re-swizzle (inverse dctex_linear) -> lz_ext_p6_fx encode
```

Three things that are easy to get wrong, all found the hard way on Ryu:

- **Ownership, not rectangles.** 2382 piece pairs across 679 of Ryu's 725 frames
  overlap, and the decode only keeps whichever piece drew last - but it skips
  *transparent* texels, so a later piece does not own a pixel it merely covers.
  Assigning ownership by rectangle instead of by opacity corrupted 0.5% of the
  pixels on 100 frames. The repack replays the decode's exact condition.
- **Undrawn tile area.** A tile is `side x side` but only `dw x dh` is ever
  sampled. Each rebuilt tile starts as a copy of the original and only the
  `dw x dh` window is overwritten, so the remainder round-trips untouched.
- **Duplicate palette entries.** CPS3 banks repeat colours, so nearest-colour
  quantisation returns visually identical but *different* indices. Keeping the
  index already in the tile when it renders the wanted colour is what takes the
  1x repack from "4826 tiles rewritten" to byte-identical.

Also note the `0x80` LZ opcode's operand is only 14 bits
(`((b & 0x3F) << 8) | next`), so its back-reference distance caps at **256**.
Encoding a longer distance silently corrupts the high byte.

### Runbook

```sh
# 0. decode the character out of your AFS (only after a decoder change)
python3 tools/cg_decode_all.py SF33RD.AFS --cg pl02.bin

# 1. repack. --src defaults to work/tiles/<stem>, falling back to
#    work/decoded/<stem>; frames must be 1x or the tool stops.
python3 tools/cg_repack.py SF33RD.AFS --cg pl02.bin --src work/decoded/pl02

#    --reencode-all pushes every tile through the LZ encoder instead of
#    keeping unchanged bytes. Slower and bigger; use it to exercise the encoder.
python3 tools/cg_repack.py SF33RD.AFS --cg pl02.bin --src work/decoded/pl02 \
  --reencode-all --out work/repacked_cg_full

# 2. verify: decode the rebuilt file and diff every frame against the PNGs
python3 tools/cg_roundtrip_check.py SF33RD.AFS work/repacked_cg/pl02.bin \
  --cg pl02.bin --ref work/decoded/pl02

# 3. if step 1 reported a NEW to_chd, update texgrpdat[] in
#    3sx/src/sf33rd/Source/Game/rendering/texgroup_data.c for that character
#    (pl02.bin is entry [3]) and rebuild the engine. to_tex never changes.

# 4. fold it into the archive alongside the stages, then verify
python3 tools/afs_rebuild.py SF33RD.AFS work/SF33RD_test.AFS \
  --replace-dir work/repacked --replace pl02.bin=work/repacked_cg/pl02.bin
python3 tools/verify_rebuild.py SF33RD.AFS work/SF33RD_test.AFS \
  pl02.bin=work/repacked_cg/pl02.bin \
  $(for f in work/repacked/*.ppg; do echo "$(basename $f)=$f"; done)
```

Verified on Ryu: step 1 at 1x reproduces the stock `pl02.bin` **byte for byte**,
and `--reencode-all` still decodes back to **725/725 frames pixel-identical**.

Only `pl02.bin` is in `TEXGROUP` (`cg_decode_tile.py`) so far. Another character
needs its `to_tex`, `to_chd`, group and palette metadata checked against
`texgrpdat[]` in `texgroup_data.c`. The HD verifier and bundle builder also have
Ryu-specific defaults that must be generalized before adding another fighter.

## Testing a rebuilt AFS in 3sx

### Where the game looks
`Resources_GetAFSPath()` (`resources.c:37-43`) returns
`Paths_GetPrefPath() + "resources/SF33RD.AFS"`, and `Paths_GetPrefPath()`
(`paths.c:10-16`) is `SDL_GetPrefPath("CrowdedStreet", "3SX")`. On Linux that is:

```
~/.local/share/CrowdedStreet/3SX/resources/SF33RD.AFS
```

The filename is **hardcoded**, but the Linux preference root follows
`XDG_DATA_HOME`. The test launcher sets that root to its own `user-data/`, so the
archive lives at `work/hd-test/user-data/CrowdedStreet/3SX/resources/SF33RD.AFS`.
Use that launcher to test without replacing the installed archive.

### The checksum gate - matters for which build you test with
`Resources_Check()` (`resources.c:45-83`) SHA-256s the whole archive and compares it
to `EXPECTED_AFS_SHA` (`resources.c:14`), but only under `#if CHECKSUM`, which
`CMakeLists.txt:73` defines **for Release builds only** (and not when
`THREESX_STATCHECK` is set):

```cmake
$<$<AND:$<CONFIG:Release>,$<NOT:$<BOOL:${THREESX_STATCHECK}>>>:CHECKSUM>
```

Any modified archive necessarily fails that hash. On failure `sdl_app.c:440-459` falls
into `Resources_RunResourceCopyingFlow()`, which prompts for a game ISO and **writes
over `resources/SF33RD.AFS`**. So test texture packs with a **Debug** or
**RelWithDebInfo** build, where `CHECKSUM` is not defined and the archive is accepted
as-is.

### Verifying a rebuild
`tools/verify_rebuild.py` re-parses both archives independently of `afs_rebuild.py`
and SHA-256s every entry payload, confirming that unchanged entries are byte-identical
and replacements match their sources. Use it rather than trusting the rebuilder's own
report.

## Current runbook

Use [hd-test-build.md](hd-test-build.md) for the isolated 4x test bundle:

```sh
python3 tools/build_hd_test.py
./work/hd-test/run.sh
```

After changing stage art, first upscale/cut/validate it and regenerate the
stage repacks with `--mode direct32`. Round-trip references are under
`work/tiles/<stage>`, not the assembled pages in `work/upscaled/<stage>`.
Use a separate output directory for experiments at another stage scale to
avoid mixing replacement files. The bundle builder consumes `work/repacked/`.

Build the engine as RelWithDebInfo (Release enables the original archive
checksum gate), and install it to include shaders:

```sh
cmake -S 3sx -B 3sx/build-test -DCMAKE_BUILD_TYPE=RelWithDebInfo
python3 tools/build_hd_test.py
```

The current source defaults already set the 4x canvas and raised memory pools.
The installed binary expects shader assets alongside it; use the bundle launcher
instead of executing the raw build-directory binary.

**Stage <-> character** (`imgui_wrapper.c:13-16`, `bg_w.stage = stage_override - 1`):
0 Gill, 1 Alex, 2 Ryu, 3 Yun, 4 Dudley, 5 Necro, 6 Hugo, 7 Ibuki, 8 Elena, 9 Oro,
10 Yang, 11 Ken, 12 Sean, 13 Urien, 14 Akuma, 15 Chun-Li, 16 Makoto, 17 Q, 18 Twelve,
19 Remy. A Debug build adds the ImGui stage-override dropdown (toggle with `` ` ``).

## Tools
- `tools/afs_extract.py <SF33RD.AFS> [-n N] [--start I]` - AFS index only; opens the
  file read-only and never touches entry payload bytes.
- `tools/ppg_inspect.py <SF33RD.AFS> <idx> [idx ...]` - walks one entry's PPG chunk
  chain and prints chunk headers only. Reads 16-byte headers; never reads,
  decompresses, or writes pixel/palette payload.
- `tools/ppg_decode_test.py <SF33RD.AFS> [--entry N] [--chunk N] [--opaque]` - format
  validation only: decodes a single 16bpp `pTEX` chunk to a PNG under
  `work/upscaled_test/` (gitignored). Not a batch extractor.
- `tools/ppg_decode_indexed.py <SF33RD.AFS> --entry N [--chunk N] [--pal-chunk N]
  [--pal-index N] [--clut-unswizzle] [--opaque]` - same, for one 8bpp indexed `pTEX`
  resolved against a `pPAL` chunk in the same entry.

### `tools/decode_stages.py`
Batch-decodes every stage background. `python3 tools/decode_stages.py SF33RD.AFS
[--only stage00,stage07] [--no-chunks]`. Writes `work/decoded/<stem>/chunk_NN.png`,
`layerN.png` (1024x512 placed grid) and `assembled.png` (layers composited).
Prints a per-stage anomaly report.

### `tools/verify_decoded.py`
Objective health check over `work/decoded/*/layer*.png`: distinct colour count,
`(0,247,0)` filler share, and tile-boundary vs. tile-interior seam energy. Prints
numbers only; no pixel data leaves the machine.

### `tools/export_for_upscale.py`
Copies (or `--link`s) chunk PNGs into `work/for_upscale/<stage>/` and writes the
round-trip contract to `work/for_upscale/README.txt`.

### `tools/ppg_repack.py`
`python3 tools/ppg_repack.py SF33RD.AFS [--stages ...] [--src DIR]
[--mode direct32|indexed]`. Rebuilds `work/repacked/<stage>.ppg`.

### `tools/roundtrip_check.py`
`python3 tools/roundtrip_check.py SF33RD.AFS work/repacked/stage00.ppg stage00`.
Decodes a repacked `.ppg` and compares every chunk to the source PNGs.

### `tools/afs_rebuild.py`
`python3 tools/afs_rebuild.py SF33RD.AFS out.AFS --replace-dir work/repacked`.
Regenerates the full index; self-verifies by re-parsing the product.

### `tools/cg_decode_tile.py`
`python3 tools/cg_decode_tile.py SF33RD.AFS [--cg pl02.bin] [--list N] [--piece N |
--all-pieces] [--bank N]`. Decodes character CG tiles to `work/upscaled_test/`,
reporting LZ bytes consumed, pixels produced, max palette index and opacity.

### `tools/check_upscaled.py`
Gate before repacking, run it first every time: filename set vs. manifest, PNG colour
type, integer and uniform scale, multiple-of-16 dimensions.

### `tools/verify_rebuild.py`
`python3 tools/verify_rebuild.py orig.AFS new.AFS NAME=path ...`. Independent
hash-level verification of a rebuilt archive.

### `tools/cg_decode_frame.py`
`python3 tools/cg_decode_frame.py SF33RD.AFS [--cg pl02.bin] [--list N ...] [--flip N]`.
Composites a whole character frame from its piece list into
`work/upscaled_test/12_<cg>_frame<NNN>.png`, applying cumulative offsets and the
`dw x dh` crop.

### `tools/cg_decode_all.py`
`python3 tools/cg_decode_all.py SF33RD.AFS [--cg pl02.bin] [--flip N]`. Decodes every
frame of one character to `work/decoded/<cg>/frame_NNN.png` with a manifest recording
each piece's frame-local rectangle (what a repack needs to re-cut tiles).
